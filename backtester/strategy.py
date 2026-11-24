"""Signal generator mirroring GoldPropGuardEA's M5/M15 XAUUSD momentum pullback logic."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .indicators import Bar, adx, atr, ema, rsi


@dataclass(frozen=True)
class StrategyConfig:
    session_start_hour: int = 8
    session_end_hour: int = 20
    max_spread_dollars: float = 0.45
    ltf_fast_ema: int = 20
    ltf_slow_ema: int = 50
    htf_fast_ema: int = 50
    htf_slow_ema: int = 200
    adx_period: int = 14
    min_adx: float = 20.0
    rsi_period: int = 14
    rsi_long_min: float = 45.0
    rsi_long_max: float = 68.0
    rsi_short_min: float = 32.0
    rsi_short_max: float = 55.0
    atr_period: int = 14
    atr_sl_mult: float = 1.50
    reward_risk_ratio: float = 1.15
    breakeven_trigger_r: float = 0.75
    min_atr_dollars: float = 0.80
    max_atr_dollars: float = 12.00


@dataclass(frozen=True)
class Signal:
    direction: str  # "BUY" or "SELL"
    sl_distance: float
    tp_distance: float


def resample_to_m15(m5_bars: List[Bar]) -> Tuple[List[Bar], Dict[int, int]]:
    """
    Aggregates M5 bars into M15 bars and maps each M5 index i -> latest CLOSED M15 bar index.
    Ensures zero lookahead bias.
    """
    m15_bars: List[Bar] = []
    m5_to_closed_m15: Dict[int, int] = {}

    current_bucket = None
    cur_bar: Optional[Bar] = None

    for idx, b in enumerate(m5_bars):
        bucket = (b.timestamp.year, b.timestamp.month, b.timestamp.day, b.timestamp.hour, b.timestamp.minute // 15)
        if bucket != current_bucket:
            if cur_bar is not None:
                m15_bars.append(cur_bar)
            current_bucket = bucket
            cur_bar = Bar(
                timestamp=b.timestamp,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                spread_dollars=b.spread_dollars,
            )
        else:
            assert cur_bar is not None
            cur_bar.high = max(cur_bar.high, b.high)
            cur_bar.low = min(cur_bar.low, b.low)
            cur_bar.close = b.close
            cur_bar.spread_dollars = b.spread_dollars

        # Only reference completed M15 bars strictly before the in-progress M15 bar
        if len(m15_bars) > 0:
            m5_to_closed_m15[idx] = len(m15_bars) - 1

    if cur_bar is not None:
        m15_bars.append(cur_bar)

    return m15_bars, m5_to_closed_m15


class MomentumPullbackStrategy:
    """Computes indicator series once and evaluates closed-bar entries."""

    def __init__(self, m5_bars: List[Bar], cfg: Optional[StrategyConfig] = None) -> None:
        self.bars = m5_bars
        self.cfg = cfg or StrategyConfig()

        closes = [b.close for b in m5_bars]
        self.ltf_fast = ema(closes, self.cfg.ltf_fast_ema)
        self.ltf_slow = ema(closes, self.cfg.ltf_slow_ema)
        self.ltf_rsi = rsi(closes, self.cfg.rsi_period)
        self.ltf_atr = atr(m5_bars, self.cfg.atr_period)

        self.m15_bars, self.m5_to_m15 = resample_to_m15(m5_bars)
        m15_closes = [b.close for b in self.m15_bars]
        self.htf_fast = ema(m15_closes, self.cfg.htf_fast_ema)
        self.htf_slow = ema(m15_closes, self.cfg.htf_slow_ema)
        self.htf_adx = adx(self.m15_bars, self.cfg.adx_period)

    def evaluate_bar_open(self, idx: int) -> Optional[Signal]:
        """Evaluates signal at bar `idx` open using strictly closed bar `idx - 1` and `idx - 2`."""
        if idx < 3 or idx not in self.m5_to_m15:
            return None

        bar_now = self.bars[idx]
        if not (self.cfg.session_start_hour <= bar_now.timestamp.hour < self.cfg.session_end_hour):
            return None
        if bar_now.spread_dollars > self.cfg.max_spread_dollars:
            return None

        htf_idx = self.m5_to_m15[idx - 1]
        htf_f = self.htf_fast[htf_idx]
        htf_s = self.htf_slow[htf_idx]
        htf_a = self.htf_adx[htf_idx]
        if htf_f is None or htf_s is None or htf_a is None or htf_a < self.cfg.min_adx:
            return None

        ltf_f = self.ltf_fast[idx - 1]
        ltf_s = self.ltf_slow[idx - 1]
        rsi1 = self.ltf_rsi[idx - 1]
        rsi2 = self.ltf_rsi[idx - 2]
        atr1 = self.ltf_atr[idx - 1]
        if None in (ltf_f, ltf_s, rsi1, rsi2, atr1):
            return None

        assert ltf_f is not None and ltf_s is not None and rsi1 is not None and rsi2 is not None and atr1 is not None
        if atr1 < self.cfg.min_atr_dollars or atr1 > self.cfg.max_atr_dollars:
            return None

        b1 = self.bars[idx - 1]
        b2 = self.bars[idx - 2]

        sl_dist = atr1 * self.cfg.atr_sl_mult
        tp_dist = sl_dist * self.cfg.reward_risk_ratio

        # Long setup
        if htf_f > htf_s and ltf_f > ltf_s:
            pullback = b1.low <= ltf_f * 1.0005 and b1.low >= ltf_s * 0.9985
            trigger = b1.close > b1.open and b1.close > ltf_f and b2.close <= ltf_f * 1.001
            momentum = self.cfg.rsi_long_min <= rsi1 <= self.cfg.rsi_long_max and rsi1 > rsi2
            if pullback and trigger and momentum:
                return Signal("BUY", sl_dist, tp_dist)

        # Short setup
        if htf_f < htf_s and ltf_f < ltf_s:
            pullback = b1.high >= ltf_f * 0.9995 and b1.high <= ltf_s * 1.0015
            trigger = b1.close < b1.open and b1.close < ltf_f and b2.close >= ltf_f * 0.999
            momentum = self.cfg.rsi_short_min <= rsi1 <= self.cfg.rsi_short_max and rsi1 < rsi2
            if pullback and trigger and momentum:
                return Signal("SELL", sl_dist, tp_dist)

        return None

"""LuxAlgo-style Smart Money Concepts (BOS/CHoCH + Order Blocks + FVG) for XAUUSD."""

from dataclasses import dataclass
from typing import List, Optional

from .indicators import Bar, atr, ema, rsi
from .strategy import Signal, resample_to_m15


@dataclass(frozen=True)
class SmcConfig:
    session_start_hour: int = 8
    session_end_hour: int = 20
    max_spread_dollars: float = 0.45
    htf_swing_len: int = 5           # M15 pivot length for BOS/CHoCH
    ltf_swing_len: int = 3           # M5 pivot length for local OB displacement
    atr_period: int = 14
    min_atr_dollars: float = 0.80
    max_atr_dollars: float = 8.50    # Keeps 0.01 lot <= 0.5% risk on $2.5k account
    min_fvg_atr_ratio: float = 0.20  # Minimum FVG size relative to ATR
    ob_lookback_bars: int = 6        # Lookback to find opposing candle before displacement
    zone_ttl_bars: int = 45          # Expire unmitigated OB/FVG after N M5 bars
    sl_atr_buffer: float = 0.35      # Buffer beyond OB/FVG boundary
    min_sl_atr_mult: float = 1.10
    max_sl_atr_mult: float = 2.20
    reward_risk_ratio: float = 1.15  # High-win-rate R:R target
    breakeven_trigger_r: float = 0.0 # Disabled by default: early BE clips Gold pullback winners
    rsi_period: int = 14
    ema_trend_period: int = 20


@dataclass
class PriceZone:
    direction: str      # "BUY" or "SELL"
    kind: str           # "OB" or "FVG"
    low: float
    high: float
    created_idx: int
    mitigated: bool = False


class SmcMomentumStrategy:
    """
    Non-repainting LuxAlgo-style Smart Money Concepts strategy:
      1. M15 Swing Structure -> BOS / CHoCH directional bias.
      2. M5 Order Blocks (displacement from opposing candle) & Fair Value Gaps.
      3. Pullback into unmitigated zone + rejection candle close + RSI/EMA confirmation.
    """

    def __init__(self, m5_bars: List[Bar], cfg: Optional[SmcConfig] = None) -> None:
        self.bars = m5_bars
        self.cfg = cfg or SmcConfig()

        closes = [b.close for b in m5_bars]
        self.ltf_atr = atr(m5_bars, self.cfg.atr_period)
        self.ltf_rsi = rsi(closes, self.cfg.rsi_period)
        self.ltf_ema = ema(closes, self.cfg.ema_trend_period)

        self.m15_bars, self.m5_to_m15 = resample_to_m15(m5_bars)
        self.htf_bias = self._compute_htf_structure_bias(self.m15_bars, self.cfg.htf_swing_len)
        self.zones_by_idx = self._precompute_m5_zones(m5_bars)

    @staticmethod
    def _compute_htf_structure_bias(bars: List[Bar], swing_len: int) -> List[int]:
        """
        Returns per-bar bias (+1 bullish, -1 bearish, 0 neutral) strictly using
        confirmed pivots (confirmed `swing_len` bars after the pivot candle).
        """
        n = len(bars)
        bias = [0] * n
        last_swing_high: Optional[float] = None
        last_swing_low: Optional[float] = None
        cur_bias = 0

        for i in range(n):
            # Confirm pivot at p = i - swing_len using [p - swing_len .. p + swing_len]
            p = i - swing_len
            if p >= swing_len:
                window = bars[p - swing_len : p + swing_len + 1]
                if bars[p].high == max(b.high for b in window):
                    last_swing_high = bars[p].high
                if bars[p].low == min(b.low for b in window):
                    last_swing_low = bars[p].low

            # BOS / CHoCH on bar close
            if last_swing_high is not None and bars[i].close > last_swing_high:
                cur_bias = 1
            elif last_swing_low is not None and bars[i].close < last_swing_low:
                cur_bias = -1

            bias[i] = cur_bias
        return bias

    def _precompute_m5_zones(self, bars: List[Bar]) -> List[List[PriceZone]]:
        """Builds active Order Block and Fair Value Gap zones up to each closed bar."""
        active: List[PriceZone] = []
        snapshots: List[List[PriceZone]] = [[] for _ in range(len(bars))]

        for i in range(len(bars)):
            a = self.ltf_atr[i]
            if a is not None and i >= 2:
                b0 = bars[i]
                b2 = bars[i - 2]

                # 1. Bullish FVG: current bar low > 2 bars ago high
                bull_gap = b0.low - b2.high
                if bull_gap >= a * self.cfg.min_fvg_atr_ratio:
                    active.append(PriceZone("BUY", "FVG", b2.high, b0.low, i))

                # 2. Bearish FVG: current bar high < 2 bars ago low
                bear_gap = b2.low - b0.high
                if bear_gap >= a * self.cfg.min_fvg_atr_ratio:
                    active.append(PriceZone("SELL", "FVG", b0.high, b2.low, i))

                # 3. Displacement Order Blocks (strong momentum bar breaking recent swing)
                body = b0.close - b0.open
                if body > a * 1.1:
                    # Find last bearish candle in lookback
                    for k in range(i - 1, max(-1, i - 1 - self.cfg.ob_lookback_bars), -1):
                        if bars[k].close < bars[k].open:
                            active.append(PriceZone("BUY", "OB", bars[k].low, bars[k].high, i))
                            break
                elif -body > a * 1.1:
                    for k in range(i - 1, max(-1, i - 1 - self.cfg.ob_lookback_bars), -1):
                        if bars[k].close > bars[k].open:
                            active.append(PriceZone("SELL", "OB", bars[k].low, bars[k].high, i))
                            break

            # Expire old zones or invalidate zones closed completely through
            kept: List[PriceZone] = []
            for z in active:
                if (i - z.created_idx) > self.cfg.zone_ttl_bars:
                    continue
                if z.direction == "BUY" and bars[i].close < z.low:
                    continue  # Bullish zone invalidated by close below
                if z.direction == "SELL" and bars[i].close > z.high:
                    continue  # Bearish zone invalidated by close above
                kept.append(z)
            active = kept[-12:]  # Keep most recent 12 active zones

            snapshots[i] = [
                PriceZone(z.direction, z.kind, z.low, z.high, z.created_idx, z.mitigated)
                for z in active
            ]

        return snapshots

    def evaluate_bar_open(self, idx: int) -> Optional[Signal]:
        """Evaluates SMC zone pullback + rejection entry at bar `idx` open."""
        if idx < 5 or idx not in self.m5_to_m15:
            return None

        bar_now = self.bars[idx]
        if not (self.cfg.session_start_hour <= bar_now.timestamp.hour < self.cfg.session_end_hour):
            return None
        if bar_now.spread_dollars > self.cfg.max_spread_dollars:
            return None

        htf_idx = self.m5_to_m15[idx - 1]
        bias = self.htf_bias[htf_idx]
        if bias == 0:
            return None

        a = self.ltf_atr[idx - 1]
        r1 = self.ltf_rsi[idx - 1]
        r2 = self.ltf_rsi[idx - 2]
        e1 = self.ltf_ema[idx - 1]
        if None in (a, r1, r2, e1):
            return None
        assert a is not None and r1 is not None and r2 is not None and e1 is not None

        if a < self.cfg.min_atr_dollars or a > self.cfg.max_atr_dollars:
            return None

        b1 = self.bars[idx - 1]
        # Look at zones active BEFORE bar idx-1 tapped them (at idx-2)
        candidate_zones = self.zones_by_idx[idx - 2]

        if bias == 1:
            # Bullish SMC: bar[idx-1] dipped into an active BUY OB/FVG and closed bullish/reclaimed
            tapped = [
                z for z in candidate_zones
                if z.direction == "BUY" and b1.low <= z.high and b1.close >= z.low
            ]
            if not tapped:
                return None
            # Confirmation: bullish candle closing above zone midpoint & momentum rising
            zone = tapped[-1]
            zone_mid = (zone.low + zone.high) / 2.0
            if b1.close > b1.open and b1.close >= zone_mid and 42.0 <= r1 <= 68.0 and r1 > r2:
                raw_sl = max(bar_now.open - (zone.low - a * self.cfg.sl_atr_buffer), a * self.cfg.min_sl_atr_mult)
                sl_dist = min(raw_sl, a * self.cfg.max_sl_atr_mult)
                return Signal("BUY", sl_dist, sl_dist * self.cfg.reward_risk_ratio)

        elif bias == -1:
            # Bearish SMC: bar[idx-1] wicked up into active SELL OB/FVG and closed bearish
            tapped = [
                z for z in candidate_zones
                if z.direction == "SELL" and b1.high >= z.low and b1.close <= z.high
            ]
            if not tapped:
                return None
            zone = tapped[-1]
            zone_mid = (zone.low + zone.high) / 2.0
            if b1.close < b1.open and b1.close <= zone_mid and 32.0 <= r1 <= 58.0 and r1 < r2:
                raw_sl = max((zone.high + a * self.cfg.sl_atr_buffer) - bar_now.open, a * self.cfg.min_sl_atr_mult)
                sl_dist = min(raw_sl, a * self.cfg.max_sl_atr_mult)
                return Signal("SELL", sl_dist, sl_dist * self.cfg.reward_risk_ratio)

        return None

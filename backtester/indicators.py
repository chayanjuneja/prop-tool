"""Pure-Python technical indicators matching MT5 standard calculations."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    spread_dollars: float = 0.25
    volume: float = 0.0


def relative_volume(bars: List[Bar], period: int = 20) -> List[Optional[float]]:
    """Computes RVOL = bar.volume / SMA(previous `period` bars volume)."""
    out: List[Optional[float]] = [None] * len(bars)
    if period <= 0 or len(bars) <= period:
        return out
    rolling_sum = sum(b.volume for b in bars[:period])
    for i in range(period, len(bars)):
        avg = rolling_sum / period
        out[i] = (bars[i].volume / avg) if avg > 0 else 1.0
        rolling_sum += bars[i].volume - bars[i - period].volume
    return out


def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def atr(bars: List[Bar], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) <= period:
        return out
    trs: List[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
        else:
            prev_c = bars[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - prev_c), abs(b.low - prev_c)))
    seed = sum(trs[1 : period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, len(bars)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        g = max(diff, 0.0)
        l = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def adx(bars: List[Bar], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) < period * 2 + 1:
        return out

    tr_list: List[float] = [0.0]
    plus_dm: List[float] = [0.0]
    minus_dm: List[float] = [0.0]

    for i in range(1, len(bars)):
        up_move = bars[i].high - bars[i - 1].high
        down_move = bars[i - 1].low - bars[i].low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        prev_c = bars[i - 1].close
        tr_list.append(max(bars[i].high - bars[i].low, abs(bars[i].high - prev_c), abs(bars[i].low - prev_c)))

    sm_tr = sum(tr_list[1 : period + 1])
    sm_plus = sum(plus_dm[1 : period + 1])
    sm_minus = sum(minus_dm[1 : period + 1])

    dx_vals: List[float] = []
    for i in range(period + 1, len(bars)):
        if i > period + 1:
            sm_tr = sm_tr - (sm_tr / period) + tr_list[i]
            sm_plus = sm_plus - (sm_plus / period) + plus_dm[i]
            sm_minus = sm_minus - (sm_minus / period) + minus_dm[i]
        p_di = 100.0 * (sm_plus / sm_tr) if sm_tr > 0 else 0.0
        m_di = 100.0 * (sm_minus / sm_tr) if sm_tr > 0 else 0.0
        denom = p_di + m_di
        dx = 100.0 * abs(p_di - m_di) / denom if denom > 0 else 0.0
        dx_vals.append(dx)
        if len(dx_vals) == period:
            out[i] = sum(dx_vals) / period
        elif len(dx_vals) > period and out[i - 1] is not None:
            out[i] = (out[i - 1] * (period - 1) + dx) / period

    return out

"""Pure-Python technical indicators (no pandas/numpy required)."""
from __future__ import annotations

from typing import Dict, List, Sequence


def ema(values: Sequence[float], period: int) -> List[float]:
    """Exponential moving average, seeded with the SMA of the first `period` values."""
    if period <= 0:
        raise ValueError("period must be > 0")
    out: List[float] = []
    if not values:
        return out
    alpha = 2.0 / (period + 1.0)
    prev: float
    for i, v in enumerate(values):
        if i < period - 1:
            window = values[0 : i + 1]
            prev = sum(window) / len(window)
        elif i == period - 1:
            window = values[0 : i + 1]
            prev = sum(window) / period
        else:
            prev = alpha * v + (1.0 - alpha) * prev
        out.append(prev)
    return out


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(candles: Sequence[Dict[str, float]], period: int = 14) -> List[float]:
    """Wilder's ATR. `candles` must have float keys h, l, c and be ordered oldest->newest."""
    out: List[float] = []
    prev_atr: float = 0.0
    for i, c in enumerate(candles):
        if i == 0:
            prev_atr = c["h"] - c["l"]
        else:
            tr = true_range(c["h"], c["l"], candles[i - 1]["c"])
            prev_atr = (prev_atr * (period - 1) + tr) / period
        out.append(prev_atr)
    return out


def sma(values: Sequence[float], period: int) -> List[float]:
    out: List[float] = []
    for i in range(len(values)):
        window = values[max(0, i - period + 1) : i + 1]
        out.append(sum(window) / len(window))
    return out


def roc(values: Sequence[float], period: int) -> List[float]:
    """Rate of change: (v[i] - v[i-period]) / v[i-period]. Earlier values are 0.0."""
    out: List[float] = []
    for i in range(len(values)):
        if i < period:
            out.append(0.0)
        else:
            prev = values[i - period]
            out.append((values[i] - prev) / prev if prev else 0.0)
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> List[float]:
    """Return the MACD histogram (macd line - signal line)."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [a - b for a, b in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)
    return [a - b for a, b in zip(macd_line, signal_line)]


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0):
    """Return (mid, upper, lower) bands."""
    mid: List[float] = []
    upper: List[float] = []
    lower: List[float] = []
    for i in range(len(values)):
        window = values[max(0, i - period + 1) : i + 1]
        m = sum(window) / len(window)
        var = sum((x - m) ** 2 for x in window) / len(window)
        sd = var ** 0.5
        mid.append(m)
        upper.append(m + mult * sd)
        lower.append(m - mult * sd)
    return mid, upper, lower


def rsi(values: Sequence[float], period: int = 14) -> List[float]:
    out: List[float] = []
    gains = 0.0
    losses = 0.0
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(len(values)):
        if i == 0:
            out.append(50.0)
            continue
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if i < period:
            gains += gain
            losses += loss
            avg_gain = gains / i
            avg_loss = losses / i
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = (avg_gain / avg_loss) if avg_loss > 0 else 999.0
        out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def _wilder_smooth(raw: Sequence[float], period: int) -> List[float]:
    """Wilder's smoothing: first value = SMA(period), then sm = prev + (x - prev)/period."""
    out: List[float] = []
    prev: float = 0.0
    for i, x in enumerate(raw):
        if i < period:
            window = raw[0 : i + 1]
            prev = sum(window) / len(window)
        elif i == period - 1:
            window = raw[0 : i + 1]
            prev = sum(window) / period
        else:
            prev = prev + (x - prev) / period
        out.append(prev)
    return out


def adx(candles: Sequence[Dict[str, float]], period: int = 14) -> List[float]:
    """Wilder's ADX. `candles` need h, l, c, ordered oldest -> newest."""
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    tr: List[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            tr.append(c["h"] - c["l"])
            continue
        prev = candles[i - 1]
        up = c["h"] - prev["h"]
        down = prev["l"] - c["l"]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(true_range(c["h"], c["l"], prev["c"]))

    sm_tr = _wilder_smooth(tr, period)
    sm_plus = _wilder_smooth(plus_dm, period)
    sm_minus = _wilder_smooth(minus_dm, period)

    dx: List[float] = []
    for i in range(len(candles)):
        tr_i = sm_tr[i]
        if tr_i <= 0:
            dx.append(0.0)
            continue
        plus_di = 100.0 * sm_plus[i] / tr_i
        minus_di = 100.0 * sm_minus[i] / tr_i
        denom = plus_di + minus_di
        dx.append(100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0)

    return _wilder_smooth(dx, period)

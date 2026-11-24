"""Step 2: signal engines.

Engine A (trend): EMA alignment + Donchian breakout + MTF agreement + flow.
Engine B (exhaustion/mean-reversion): RSI extreme + Bollinger touch + flow turn,
only in a non-trending regime (ADX low), to avoid fighting a trend.

Each engine returns a Signal with a score and human-readable reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .indicators import adx, atr, bollinger, ema, rsi

DONCHIAN_N = 20


@dataclass
class Signal:
    engine: str
    coin: str
    side: str
    score: int
    reasons: List[str] = field(default_factory=list)
    entry_hint: float = 0.0
    stop_hint: float = 0.0
    atr: float = 0.0


def engine_a(coin: str, candles: Sequence[dict], mtf_dirs: Sequence[int],
             cvd: Optional[float], imb: Optional[float],
             min_score: int = 3, mtf_min: int = 2) -> Optional[Signal]:
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    v = [c["v"] for c in candles]
    if len(closes) < 60:
        return None
    i = len(closes) - 1
    price = closes[i]
    ef = ema(closes, 20)[i]
    es = ema(closes, 50)[i]
    a = atr(candles, 14)[i]
    up = max(highs[i - DONCHIAN_N : i])
    dn = min(lows[i - DONCHIAN_N : i])
    vol_ratio = (sum(v[-20:]) / 20.0) / (sum(v[-100:]) / 100.0) if len(v) >= 100 else 0.0

    side = None
    reasons: List[str] = []
    score = 0
    if price > ef > es:
        side = "long"
        score += 1
        reasons.append("EMA多头排列")
    elif price < ef < es:
        side = "short"
        score += 1
        reasons.append("EMA空头排列")
    if side is None:
        return None
    if (side == "long" and price > up) or (side == "short" and price < dn):
        score += 1
        reasons.append("Donchian突破")
    agree = sum(1 for d in mtf_dirs if (side == "long" and d > 0) or (side == "short" and d < 0))
    if agree >= mtf_min:
        score += 1
        reasons.append(f"多周期{agree}/3同向")
    if cvd is not None and ((side == "long" and cvd > 0.1) or (side == "short" and cvd < -0.1)):
        score += 1
        reasons.append("CVD同向")
    if imb is not None and ((side == "long" and imb > 0.1) or (side == "short" and imb < -0.1)):
        score += 1
        reasons.append("盘口失衡同向")
    if vol_ratio >= 1.2:
        score += 1
        reasons.append("放量")
    if score < min_score:
        return None
    sign = 1 if side == "long" else -1
    return Signal("A", coin, side, score, reasons,
                  entry_hint=price, stop_hint=price - sign * 3.0 * a, atr=a)


def engine_b(coin: str, candles: Sequence[dict], cvd: Optional[float],
             imb: Optional[float]) -> Optional[Signal]:
    closes = [c["c"] for c in candles]
    if len(closes) < 60:
        return None
    i = len(closes) - 1
    price = closes[i]
    a = atr(candles, 14)[i]
    r = rsi(closes, 14)[i]
    _, upper, lower = bollinger(closes, 20, 2.0)
    adx_v = adx(candles, 14)[i]
    if adx_v is not None and adx_v > 25:
        return None  # trending: don't fade

    side = None
    reasons: List[str] = []
    if r < 30 and price <= lower[i] * 1.005:
        side = "long"
        reasons.append(f"RSI超卖{r:.0f}")
        reasons.append("触布林下轨")
    elif r > 70 and price >= upper[i] * 0.995:
        side = "short"
        reasons.append(f"RSI超买{r:.0f}")
        reasons.append("触布林上轨")
    if side is None:
        return None
    if cvd is not None and ((side == "long" and cvd > 0) or (side == "short" and cvd < 0)):
        reasons.append("CVD转向")
    else:
        return None
    if imb is not None and ((side == "long" and imb > 0) or (side == "short" and imb < 0)):
        reasons.append("盘口承接")
    sign = 1 if side == "long" else -1
    return Signal("B", coin, side, 2, reasons,
                  entry_hint=price, stop_hint=price - sign * 2.0 * a, atr=a)


def engine_c(coin: str, candles: Sequence[dict], basis: Optional[float]) -> Optional[Signal]:
    if basis is None or abs(basis) < 0.5:
        return None
    closes = [c["c"] for c in candles]
    if len(closes) < 60:
        return None
    price = closes[-1]
    a = atr(candles, 14)[-1]
    side = "long" if basis < -0.5 else "short"
    sign = 1 if side == "long" else -1
    reason = f"贴水{basis:+.2f}%" if basis < 0 else f"升水{basis:+.2f}%"
    return Signal("C", coin, side, 2, [reason, "基差回归"],
                  entry_hint=price, stop_hint=price - sign * 2.0 * a, atr=a)


def evaluate(coin: str, candles: Sequence[dict], mtf_dirs: Sequence[int],
             cvd: Optional[float], imb: Optional[float],
             basis: Optional[float] = None, min_score: int = 3, mtf_min: int = 2) -> List[Signal]:
    out: List[Signal] = []
    a = engine_a(coin, candles, mtf_dirs, cvd, imb, min_score, mtf_min)
    if a is not None:
        out.append(a)
    b = engine_b(coin, candles, cvd, imb)
    if b is not None:
        out.append(b)
    c = engine_c(coin, candles, basis)
    if c is not None:
        out.append(c)
    return out

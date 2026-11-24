"""Strategy: dual-EMA crossover with optional trend/ADX/volatility/momentum/funding filters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from .config import Config
from .indicators import adx, atr, ema, roc


@dataclass
class IndicatorSnapshot:
    ema_fast: float
    ema_slow: float
    ema_trend: Optional[float]
    atr: float
    adx: Optional[float]
    roc: Optional[float]
    funding: Optional[float]
    close: float
    cross_up: bool    # fast crossed above slow on the most recent bar
    cross_down: bool  # fast crossed below slow on the most recent bar


def compute_indicators(
    candles: Sequence[Dict[str, float]],
    cfg: Config,
    funding: Optional[float] = None,
) -> IndicatorSnapshot:
    if len(candles) < 3:
        raise ValueError("need at least 3 candles to compute indicators")
    closes = [c["c"] for c in candles]
    ema_fast = ema(closes, cfg.ema_fast)
    ema_slow = ema(closes, cfg.ema_slow)
    atr_series = atr(candles, cfg.atr_period)

    fast_now = ema_fast[-1]
    slow_now = ema_slow[-1]
    fast_prev = ema_fast[-2]
    slow_prev = ema_slow[-2]

    cross_up = fast_prev <= slow_prev and fast_now > slow_now
    cross_down = fast_prev >= slow_prev and fast_now < slow_now

    ema_trend_val: Optional[float] = None
    if cfg.use_trend_filter and len(closes) >= cfg.ema_trend:
        ema_trend_val = ema(closes, cfg.ema_trend)[-1]

    adx_val: Optional[float] = None
    if cfg.adx_min > 0 and len(closes) >= cfg.adx_period + 2:
        adx_val = adx(candles, cfg.adx_period)[-1]

    roc_val: Optional[float] = None
    if cfg.momentum_period > 0 and len(closes) > cfg.momentum_period:
        roc_val = roc(closes, cfg.momentum_period)[-1]

    return IndicatorSnapshot(
        ema_fast=fast_now,
        ema_slow=slow_now,
        ema_trend=ema_trend_val,
        atr=atr_series[-1],
        adx=adx_val,
        roc=roc_val,
        funding=funding,
        close=closes[-1],
        cross_up=cross_up,
        cross_down=cross_down,
    )


# ------------------------------------------------------------------ filters
def trend_allows_long(snap: IndicatorSnapshot, cfg: Config) -> bool:
    if not cfg.use_trend_filter or snap.ema_trend is None:
        return True
    return snap.close > snap.ema_trend


def trend_allows_short(snap: IndicatorSnapshot, cfg: Config) -> bool:
    if not cfg.use_trend_filter or snap.ema_trend is None:
        return True
    return snap.close < snap.ema_trend


def adx_allows(snap: IndicatorSnapshot, cfg: Config) -> bool:
    """Require sufficient trend strength. Disabled when adx_min <= 0 or data insufficient."""
    if cfg.adx_min <= 0 or snap.adx is None:
        return True
    return snap.adx >= cfg.adx_min


def volatility_allows(snap: IndicatorSnapshot, cfg: Config) -> bool:
    """Skip entries when ATR relative to price is too high (chaotic market)."""
    if cfg.atr_max_pct <= 0 or snap.close <= 0:
        return True
    return (snap.atr / snap.close) <= cfg.atr_max_pct


def momentum_allows_long(snap: IndicatorSnapshot, cfg: Config) -> bool:
    if cfg.momentum_period <= 0 or snap.roc is None:
        return True
    return snap.roc >= cfg.momentum_min


def momentum_allows_short(snap: IndicatorSnapshot, cfg: Config) -> bool:
    if cfg.momentum_period <= 0 or snap.roc is None:
        return True
    return snap.roc <= -cfg.momentum_min


def funding_allows_long(snap: IndicatorSnapshot, cfg: Config) -> bool:
    """Avoid longing a coin with extreme positive funding (crowded longs)."""
    if not cfg.funding_filter or snap.funding is None:
        return True
    return snap.funding <= cfg.funding_max_abs


def funding_allows_short(snap: IndicatorSnapshot, cfg: Config) -> bool:
    """Avoid shorting a coin with extreme negative funding (crowded shorts)."""
    if not cfg.funding_filter or snap.funding is None:
        return True
    return snap.funding >= -cfg.funding_max_abs


def long_ok(snap: IndicatorSnapshot, cfg: Config) -> bool:
    return (
        trend_allows_long(snap, cfg)
        and adx_allows(snap, cfg)
        and volatility_allows(snap, cfg)
        and momentum_allows_long(snap, cfg)
        and funding_allows_long(snap, cfg)
    )


def short_ok(snap: IndicatorSnapshot, cfg: Config) -> bool:
    return (
        trend_allows_short(snap, cfg)
        and adx_allows(snap, cfg)
        and volatility_allows(snap, cfg)
        and momentum_allows_short(snap, cfg)
        and funding_allows_short(snap, cfg)
    )


# ---------------------------------------------------------- stops and targets
def stop_and_target(side: str, entry: float, atr_value: float, cfg: Config):
    """Return (stop_px, target_px_or_None)."""
    if side == "long":
        stop = entry - cfg.stop_atr_multiple * atr_value
        target = None
        if cfg.take_profit_atr_multiple and cfg.take_profit_atr_multiple > 0:
            target = entry + cfg.take_profit_atr_multiple * atr_value
    else:
        stop = entry + cfg.stop_atr_multiple * atr_value
        target = None
        if cfg.take_profit_atr_multiple and cfg.take_profit_atr_multiple > 0:
            target = entry - cfg.take_profit_atr_multiple * atr_value
    return stop, target


def stop_hit(side: str, mark: float, stop: Optional[float], target: Optional[float]) -> bool:
    if side == "long":
        if stop is not None and mark <= stop:
            return True
        if target is not None and mark >= target:
            return True
    else:
        if stop is not None and mark >= stop:
            return True
        if target is not None and mark <= target:
            return True
    return False

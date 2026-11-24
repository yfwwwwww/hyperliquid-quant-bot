"""Position sizing and rounding helpers."""
from __future__ import annotations

import math
from typing import Optional

from .config import Config


def round_size_down(size: float, sz_decimals: int) -> float:
    """Round a size DOWN to the exchange's size decimals (never round up)."""
    factor = 10 ** sz_decimals
    return math.floor(size * factor) / factor


def compute_position_size(
    equity: float,
    price: float,
    atr_value: float,
    sz_decimals: int,
    cfg: Config,
) -> Optional[float]:
    """Risk-based position size in units of the base coin, or None if too small.

    size = min(risk_amount / stop_distance, max_notional / price)
    where risk_amount = equity * risk_per_trade, max_notional = equity * leverage * max_exposure.
    """
    if equity <= 0 or price <= 0 or atr_value <= 0:
        return None

    risk_amount = equity * cfg.risk_per_trade
    stop_distance = cfg.stop_atr_multiple * atr_value
    size_by_risk = risk_amount / stop_distance

    max_notional = equity * cfg.leverage * cfg.max_exposure
    size_by_cap = max_notional / price

    size = min(size_by_risk, size_by_cap)
    size = round_size_down(size, sz_decimals)

    if size <= 0:
        return None
    if size * price < cfg.min_notional:
        return None
    return size

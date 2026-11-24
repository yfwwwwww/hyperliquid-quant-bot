"""Step 3: execution layer (paper).

Maker-first entries: place a passive limit slightly behind the market. If the
price trades through it within `maker_timeout_seconds`, fill at the limit and
pay the maker fee (often a rebate). Otherwise cancel and optionally take the
market (taker fee).

Exits (stop / TP1 / TP2) are taker for certainty.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .config import Config


@dataclass
class PendingOrder:
    coin: str
    side: str
    qty: float
    limit: float
    created: float
    stop: float
    tp1: float
    tp2: float
    atr: float


def maker_limit_price(side: str, mark: float, atr: float, offset_atr: float) -> float:
    if side == "long":
        return mark - offset_atr * atr
    return mark + offset_atr * atr


def touched(p: PendingOrder, mark: float) -> bool:
    if p.side == "long":
        return mark <= p.limit
    return mark >= p.limit


def expired(p: PendingOrder, timeout_seconds: int, now: Optional[float] = None) -> bool:
    return (now or time.time()) - p.created >= timeout_seconds


def maker_entry_fee(cfg: Config, price: float, qty: float) -> float:
    return price * qty * cfg.maker_fee_rate


def taker_entry_fee(cfg: Config, price: float, qty: float) -> float:
    return price * qty * cfg.fee_rate

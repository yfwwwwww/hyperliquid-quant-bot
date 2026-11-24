"""Order-flow signals: L2 orderbook imbalance and taker buy/sell fraction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .client import HyperClient
from .config import Config


@dataclass
class Orderflow:
    imbalance: Optional[float]      # -1..1, positive = bid-heavy
    taker_buy_frac: Optional[float]  # 0..1, fraction of taker notional that was buy


def orderbook_imbalance(client: HyperClient, coin: str, levels: int = 20) -> Optional[float]:
    try:
        snap = client.info.l2_snapshot(coin)
        bids = snap["levels"][0]
        asks = snap["levels"][1]
    except Exception:
        return None
    bid_sz = sum(float(lvl["sz"]) for lvl in bids[:levels])
    ask_sz = sum(float(lvl["sz"]) for lvl in asks[:levels])
    total = bid_sz + ask_sz
    if total <= 0:
        return None
    return (bid_sz - ask_sz) / total


def read_orderflow(
    client: HyperClient,
    feed,
    coin: str,
    cfg: Config,
) -> Orderflow:
    imbalance = orderbook_imbalance(client, coin, cfg.orderbook_levels)
    taker = None
    if feed is not None:
        taker = feed.taker_buy_fraction(coin, cfg.taker_window_seconds)
    return Orderflow(imbalance=imbalance, taker_buy_frac=taker)


def allows(of: Orderflow, side: str, cfg: Config) -> bool:
    """Entry gate: bid support for longs, ask pressure for shorts."""
    if side == "long":
        if of.imbalance is not None and of.imbalance < cfg.orderflow_min_imb:
            return False
        if of.taker_buy_frac is not None and of.taker_buy_frac < cfg.orderflow_taker_min:
            return False
        return True
    if of.imbalance is not None and of.imbalance > -cfg.orderflow_min_imb:
        return False
    if of.taker_buy_frac is not None and of.taker_buy_frac > 1 - cfg.orderflow_taker_min:
        return False
    return True


def orderbook_walls(client, coin: str, levels: int = 50, pct: float = 0.5):
    """Largest bid/ask size within `pct`% of mid, plus a wall imbalance."""
    try:
        snap = client.info.l2_snapshot(coin)
        levels_data = snap.get("levels") or []
        if len(levels_data) < 2 or not levels_data[0] or not levels_data[1]:
            return None
        bids, asks = levels_data[0][:levels], levels_data[1][:levels]
        mid = (float(bids[0]["px"]) + float(asks[0]["px"])) / 2.0
        lo, hi = mid * (1 - pct / 100.0), mid * (1 + pct / 100.0)
        bid_wall = max((float(b["sz"]) for b in bids if float(b["px"]) >= lo), default=0.0)
        ask_wall = max((float(a["sz"]) for a in asks if float(a["px"]) <= hi), default=0.0)
        total = bid_wall + ask_wall
        return {
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_imb": ((bid_wall - ask_wall) / total) if total > 0 else 0.0,
        }
    except Exception:
        return None

"""Account abstraction: paper (local simulation) and live (Hyperliquid API), multi-coin."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .client import HyperClient
from .config import Config

log = logging.getLogger("hyperbot.account")


@dataclass
class Position:
    side: Optional[str] = None  # "long" | "short" | None
    size: float = 0.0
    entry: float = 0.0
    stop: Optional[float] = None
    target: Optional[float] = None

    def is_open(self) -> bool:
        return self.side is not None and self.size > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "size": self.size,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Position":
        if not d:
            return cls()
        return cls(
            side=d.get("side"),
            size=float(d.get("size", 0.0) or 0.0),
            entry=float(d.get("entry", 0.0) or 0.0),
            stop=d.get("stop"),
            target=d.get("target"),
        )


class State:
    """JSON-persisted state: daily guard, per-coin cooldown, per-coin positions."""

    def __init__(self, path: str):
        self.path = path
        self.day: str = _utc_day()
        self.day_start_equity: float = 0.0
        self.cooldown_until: Dict[str, float] = {}
        self.paper_equity: float = 0.0
        self.paper_positions: Dict[str, Position] = {}
        self.paper_trades: List[Dict[str, Any]] = []
        self.live_positions: Dict[str, Position] = {}

    # ------------------------------------------------------------- persistence
    def save(self) -> None:
        data = {
            "day": self.day,
            "day_start_equity": self.day_start_equity,
            "cooldown_until": self.cooldown_until,
            "paper_equity": self.paper_equity,
            "paper_positions": {c: p.to_dict() for c, p in self.paper_positions.items()},
            "paper_trades": self.paper_trades[-500:],
            "live_positions": {c: p.to_dict() for c, p in self.live_positions.items()},
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> "State":
        state = cls(path)
        if not os.path.exists(path):
            return state
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        state.day = data.get("day", state.day)
        state.day_start_equity = float(data.get("day_start_equity", 0.0) or 0.0)
        state.cooldown_until = {k: float(v) for k, v in (data.get("cooldown_until") or {}).items()}
        state.paper_equity = float(data.get("paper_equity", 0.0) or 0.0)
        state.paper_positions = {
            c: Position.from_dict(p) for c, p in (data.get("paper_positions") or {}).items()
        }
        state.paper_trades = data.get("paper_trades", [])
        state.live_positions = {
            c: Position.from_dict(p) for c, p in (data.get("live_positions") or {}).items()
        }
        return state

    # -------------------------------------------------------------- daily guard
    def ensure_day(self) -> None:
        today = _utc_day()
        if today != self.day:
            self.day = today
            self.day_start_equity = 0.0  # caller sets it to current equity
            self.paper_trades = []

    def daily_halted(self, equity: float, cfg: Config) -> bool:
        if self.day_start_equity <= 0:
            return False
        return equity <= self.day_start_equity * (1.0 - cfg.max_daily_loss_pct)

    def in_cooldown(self, coin: str) -> bool:
        return time.time() < self.cooldown_until.get(coin, 0.0)

    def set_cooldown(self, coin: str, seconds: int) -> None:
        self.cooldown_until[coin] = time.time() + seconds


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ==================================================================== accounts
class PaperAccount:
    """Local simulation: fills at mid +/- slippage, minus a fee. Multi-coin."""

    def __init__(self, state: State, cfg: Config):
        self.state = state
        self.cfg = cfg
        if state.paper_equity <= 0:
            state.paper_equity = cfg.paper_start_equity

    def realized_equity(self) -> float:
        return self.state.paper_equity

    def position(self, coin: str) -> Position:
        return self.state.paper_positions.get(coin, Position())

    def positions(self) -> Dict[str, Position]:
        return self.state.paper_positions

    def open_count(self) -> int:
        return sum(1 for p in self.state.paper_positions.values() if p.is_open())

    def unrealized_pnl(self, coin: str, mark: float) -> float:
        pos = self.state.paper_positions.get(coin)
        if pos is None or not pos.is_open():
            return 0.0
        if pos.side == "long":
            return (mark - pos.entry) * pos.size
        return (pos.entry - mark) * pos.size

    def equity_at_mark(self, marks: Dict[str, float]) -> float:
        total = self.state.paper_equity
        for coin, mark in marks.items():
            total += self.unrealized_pnl(coin, mark)
        return total

    # ---------------------------------------------------------------- trading
    def open(self, coin: str, side: str, size: float, price: float, stop: Optional[float], target: Optional[float]) -> None:
        pos = self.state.paper_positions.get(coin)
        if pos is None or not pos.is_open():
            pos = Position()
            self.state.paper_positions[coin] = pos
        else:
            self.close(coin, price)
            pos = self.state.paper_positions[coin]
        fill = price * (1 + self.cfg.slippage) if side == "long" else price * (1 - self.cfg.slippage)
        fee = fill * size * self.cfg.fee_rate
        self.state.paper_equity -= fee
        pos.side = side
        pos.size = size
        pos.entry = fill
        pos.stop = stop
        pos.target = target

    def close(self, coin: str, price: float) -> float:
        pos = self.state.paper_positions.get(coin)
        if pos is None or not pos.is_open():
            return 0.0
        if pos.side == "long":
            fill = price * (1 - self.cfg.slippage)
            gross = (fill - pos.entry) * pos.size
        else:
            fill = price * (1 + self.cfg.slippage)
            gross = (pos.entry - fill) * pos.size
        fee = fill * pos.size * self.cfg.fee_rate
        net = gross - fee
        self.state.paper_equity += net
        self.state.paper_trades.append(
            {
                "coin": coin,
                "side": pos.side,
                "entry": pos.entry,
                "exit": fill,
                "size": pos.size,
                "pnl": net,
                "t": int(time.time()),
            }
        )
        pos.side = None
        pos.size = 0.0
        pos.entry = 0.0
        pos.stop = None
        pos.target = None
        return net


class LiveAccount:
    """Real orders via Hyperliquid; stop/target levels persisted in state. Multi-coin."""

    def __init__(self, state: State, client: HyperClient):
        self.state = state
        self.client = client

    def position(self, coin: str) -> Position:
        return self.state.live_positions.get(coin, Position())

    def positions(self) -> Dict[str, Position]:
        return self.state.live_positions

    def open_count(self) -> int:
        return sum(1 for p in self.state.live_positions.values() if p.is_open())

    def sync_positions(self) -> None:
        """Reconcile local metadata with the exchange's actual positions."""
        api_positions = self.client.get_positions_all()
        for coin in self.client.cfg.coins:
            meta = self.state.live_positions.get(coin)
            api = api_positions.get(coin)
            if api is None:
                if meta is not None and meta.is_open():
                    self.state.live_positions[coin] = Position()
                continue
            if meta is None:
                meta = Position()
                self.state.live_positions[coin] = meta
            meta.side = api["side"]
            meta.size = api["size"]
            if api["entry"]:
                meta.entry = api["entry"]
            # preserve stop/target if already set

    def set_stop_target(self, coin: str, stop: Optional[float], target: Optional[float]) -> None:
        meta = self.state.live_positions.setdefault(coin, Position())
        meta.stop = stop
        meta.target = target

    def open(self, coin: str, side: str, size: float, price: float, stop: Optional[float], target: Optional[float]) -> None:
        is_buy = side == "long"
        resp = self.client.market_open(coin, is_buy, size, self.client.cfg.slippage)
        if not self.client.ok(resp):
            raise RuntimeError(f"order rejected for {coin}: {resp}")

        meta = self.state.live_positions.setdefault(coin, Position())
        meta.side = side
        meta.size = size
        meta.stop = stop
        meta.target = target
        meta.entry = price  # best-effort; corrected on next sync_positions()

        # Optional exchange-side TP/SL trigger orders (crash-safe stops).
        if self.client.cfg.use_native_tpsl:
            close_is_buy = side != "long"  # closing a long sells, closing a short buys
            try:
                if stop is not None:
                    r = self.client.place_trigger(coin, close_is_buy, size, stop, "sl")
                    if not self.client.ok(r):
                        log.warning("native stop-loss rejected for %s: %s", coin, r)
                if target is not None:
                    r = self.client.place_trigger(coin, close_is_buy, size, target, "tp")
                    if not self.client.ok(r):
                        log.warning("native take-profit rejected for %s: %s", coin, r)
            except Exception:
                log.exception("native TP/SL placement failed for %s; client-side stop still active", coin)

    def open_maker(self, coin: str, side: str, size: float, limit: float,
                   tif: str = "Alo") -> Optional[int]:
        """Place a post-only (ALO) limit order. Returns the resting oid, or None."""
        is_buy = side == "long"
        try:
            resp = self.client.exchange.order(
                coin, is_buy, size, limit, {"limit": {"tif": tif}}, reduce_only=False
            )
        except Exception:
            log.exception("maker order failed for %s", coin)
            return None
        if not self.client.ok(resp):
            log.warning("maker order rejected for %s: %s", coin, resp)
            return None
        try:
            st = resp["response"]["data"]["statuses"][0]
            if "resting" in st:
                return int(st["resting"]["oid"])
            if "filled" in st:
                return int(st["filled"]["oid"])
        except Exception:
            pass
        return None

    def cancel_order(self, coin: str, oid: int) -> bool:
        try:
            resp = self.client.exchange.cancel(coin, oid)
            return self.client.ok(resp)
        except Exception:
            log.exception("cancel failed for %s oid=%s", coin, oid)
            return False

    def open_orders(self) -> List[Dict[str, Any]]:
        try:
            return self.client.info.open_orders(self.client.address())
        except Exception:
            return []

    def close(self, coin: str, price: float = 0.0) -> float:
        if self.client.cfg.use_native_tpsl:
            try:
                self.client.cancel_open_orders(coin)
            except Exception:
                log.exception("cancel open orders failed for %s; proceeding to market close", coin)
        resp = self.client.market_close(coin, None, self.client.cfg.slippage)
        if not self.client.ok(resp):
            raise RuntimeError(f"close rejected for {coin}: {resp}")
        self.state.live_positions[coin] = Position()
        return 0.0

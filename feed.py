"""Optional real-time feed: bbo prices and taker (trade) flow via websocket.

The polling loop remains the source of truth for candle-based decisions, so a
websocket failure degrades gracefully to polling only.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from .config import Config

log = logging.getLogger("hyperbot.feed")

Trade = Dict[str, float]  # {"ts": float, "side": "buy"/"sell", "notional": float}


class RealtimeFeed:
    def __init__(self, cfg: Config, base_url: str):
        self.cfg = cfg
        self._base_url = base_url
        self._prices: Dict[str, float] = {}
        self._trades: Dict[str, Deque[Trade]] = {}
        self._lock = threading.Lock()
        self._info = None
        self._started = False

    def start(self, coins: List[str]) -> None:
        if self._started:
            return
        try:
            from hyperliquid.info import Info

            self._info = Info(self._base_url)  # websocket enabled
            for coin in coins:
                self._info.subscribe({"type": "bbo", "coin": coin}, self._on_bbo)
                self._info.subscribe({"type": "trades", "coin": coin}, self._on_trades)
            self._started = True
            log.info("websocket feed started for %s", coins)
        except Exception as exc:  # degrade to polling only
            self._started = False
            log.warning("websocket feed unavailable (%s); falling back to polling", exc)

    # ------------------------------------------------------------------ bbo
    def _on_bbo(self, msg) -> None:
        try:
            data = msg.get("data", {})
            coin = data.get("coin")
            levels = data.get("levels") or []
            if coin and len(levels) >= 2 and levels[0] and levels[1]:
                bid = float(levels[0][0])
                ask = float(levels[1][0])
                if bid > 0 and ask > 0:
                    with self._lock:
                        self._prices[coin] = (bid + ask) / 2.0
        except Exception:
            log.debug("bad bbo message", exc_info=True)

    def latest_price(self, coin: str) -> Optional[float]:
        with self._lock:
            return self._prices.get(coin)

    # ---------------------------------------------------------------- trades
    def _on_trades(self, msg) -> None:
        try:
            data = msg.get("data")
            trades = data if isinstance(data, list) else [data]
            now = time.time()
            for t in trades:
                if not isinstance(t, dict):
                    continue
                coin = t.get("coin")
                side_raw = t.get("side")
                px = float(t.get("px") or 0.0)
                sz = float(t.get("sz") or 0.0)
                if not coin or px <= 0 or sz <= 0:
                    continue
                side = "buy" if side_raw == "B" else "sell"
                with self._lock:
                    dq = self._trades.setdefault(coin, deque(maxlen=5000))
                    dq.append({"ts": now, "side": side, "notional": px * sz})
        except Exception:
            log.debug("bad trades message", exc_info=True)

    def taker_buy_fraction(self, coin: str, window_sec: int) -> Optional[float]:
        """Fraction of traded notional that was taker-buy over the last window."""
        cutoff = time.time() - window_sec
        buy = sell = 0.0
        with self._lock:
            dq = self._trades.get(coin)
            if not dq:
                return None
            for t in dq:
                if t["ts"] < cutoff:
                    continue
                if t["side"] == "buy":
                    buy += t["notional"]
                else:
                    sell += t["notional"]
        total = buy + sell
        if total <= 0:
            return None
        return buy / total

    def taker_delta(self, coin: str, window_sec: int) -> Optional[float]:
        """Net taker flow as a fraction in [-1, 1]: (buy - sell) / total notional."""
        cutoff = time.time() - window_sec
        buy = sell = 0.0
        with self._lock:
            dq = self._trades.get(coin)
            if not dq:
                return None
            for t in dq:
                if t["ts"] < cutoff:
                    continue
                if t["side"] == "buy":
                    buy += t["notional"]
                else:
                    sell += t["notional"]
        total = buy + sell
        if total <= 0:
            return None
        return (buy - sell) / total

    def stop(self) -> None:
        try:
            if self._info is not None:
                self._info.disconnect_websocket()
        except Exception:
            pass
        self._started = False

"""Thin wrapper around the official Hyperliquid SDK (Info + Exchange)."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .config import Config, interval_to_ms


def _is_rate_limit(exc: Exception) -> bool:
    s = str(exc).lower()
    return "429" in s or "too many" in s or "rate limit" in s


def _retry(fn: Callable[[], Any], attempts: int = 3, base_delay: float = 1.0) -> Any:
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_rate_limit(exc):
                time.sleep(base_delay * (2 ** i))
                continue
            raise
    assert last is not None
    raise last


class HyperClient:
    """Unified access to Hyperliquid market data and (optionally) order placement."""

    def __init__(self, cfg: Config, trading: bool = True):
        self.cfg = cfg
        self._sz_decimals: Dict[str, int] = {}
        self._asset_ctxs: Dict[str, Dict[str, Any]] = {}
        self._base_url = self._resolve_base_url()

        from hyperliquid.info import Info

        # skip_ws=True avoids spinning up a websocket thread; we poll instead.
        self.info = _retry(lambda: Info(self._base_url, skip_ws=True, perp_dexs=cfg.perp_dexs or None))

        self.exchange = None
        if trading and cfg.mode in ("testnet", "mainnet"):
            import eth_account
            from hyperliquid.exchange import Exchange

            account = eth_account.Account.from_key(cfg.private_key)
            self.exchange = Exchange(
                account,
                self._base_url,
                account_address=cfg.account_address,
                perp_dexs=cfg.perp_dexs or None,
            )
        if trading:
            _retry(self._load_meta)

    def _dexs(self) -> List[str]:
        return [""] + list(self.cfg.perp_dexs)

    # ------------------------------------------------------------------ setup
    def _resolve_base_url(self) -> str:
        from hyperliquid.utils import constants

        if self.cfg.mode == "testnet":
            return constants.TESTNET_API_URL
        # paper mode reads REAL public market data (no key required).
        return constants.MAINNET_API_URL

    def _load_meta(self) -> None:
        meta_and_ctxs = self.info.meta_and_asset_ctxs()
        universe = meta_and_ctxs[0]["universe"]
        asset_ctxs = meta_and_ctxs[1]
        for i, item in enumerate(universe):
            name = item["name"]
            self._sz_decimals[name] = int(item["szDecimals"])
            if i < len(asset_ctxs):
                self._asset_ctxs[name] = asset_ctxs[i]
        # builder-deployed perp dexs (e.g. "xyz" stocks/commodities)
        for dex in self.cfg.perp_dexs:
            meta = _retry(lambda d=dex: self.info.meta(dex=d))
            for item in meta.get("universe", []):
                self._sz_decimals[item["name"]] = int(item["szDecimals"])

    # ------------------------------------------------------------------ data
    @property
    def base_url(self) -> str:
        return self._base_url

    def address(self) -> str:
        # user_state must be queried with the MAIN account address.
        if self.cfg.account_address:
            return self.cfg.account_address
        if self.exchange is not None:
            return self.exchange.wallet.address
        return ""

    def list_coins(self) -> List[str]:
        return list(self._sz_decimals.keys())

    def sz_decimals(self, coin: str) -> int:
        if coin not in self._sz_decimals:
            raise ValueError(f"unknown coin {coin!r}; check your config and listed markets")
        return self._sz_decimals[coin]

    def get_funding(self, coin: str) -> Optional[float]:
        """Current funding rate (per hour, as a fraction) for `coin`."""
        ctx = self._asset_ctxs.get(coin)
        if not ctx or ctx.get("funding") is None:
            return None
        return float(ctx["funding"])

    def get_funding_history(self, coin: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
        return _retry(lambda: self.info.funding_history(coin, start_ms, end_ms))

    def get_candles(self, coin: str, interval: str, count: int) -> List[Dict[str, float]]:
        count = min(count, 5000)
        interval_ms = interval_to_ms(interval)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (count + 5) * interval_ms
        raw = _retry(lambda: self.info.candles_snapshot(coin, interval, start_ms, end_ms))
        candles = []
        for c in raw:
            candles.append(
                {
                    "t": int(c["t"]),
                    "o": float(c["o"]),
                    "h": float(c["h"]),
                    "l": float(c["l"]),
                    "c": float(c["c"]),
                    "v": float(c["v"]),
                }
            )
        return candles[-count:]

    def asset_ctx(self, coin: str) -> Dict[str, Any]:
        ctx = getattr(self, "_asset_ctxs", {}).get(coin)
        return ctx or {}

    def get_spread(self, coin: str) -> Optional[float]:
        """Return (ask-bid)/mid in bps, or None."""
        try:
            snap = self.info.l2_snapshot(coin)
            levels = snap.get("levels") or []
            if len(levels) < 2 or not levels[0] or not levels[1]:
                return None
            bid = float(levels[0][0]["px"])
            ask = float(levels[1][0]["px"])
            mid = (bid + ask) / 2.0
            return ((ask - bid) / mid * 10000.0) if mid > 0 else None
        except Exception:
            return None

    def get_mid(self, coin: str) -> float:
        mids = self.get_mids_all()
        if coin not in mids:
            raise ValueError(f"coin {coin!r} not found in allMids")
        return float(mids[coin])

    def get_mids_all(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for dex in self._dexs():
            raw = _retry(lambda d=dex: self.info.all_mids(dex=d))
            out.update({k: float(v) for k, v in raw.items()})
        return out

    def get_user_state(self, dex: str = "") -> Dict[str, Any]:
        return _retry(lambda: self.info.user_state(self.address(), dex))

    def get_portfolio(self) -> Any:
        now = time.time()
        cached = getattr(self, "_portfolio_cache", None)
        if cached and now - cached[0] < 10.0:
            return cached[1]
        data = _retry(lambda: self.info.portfolio(self.address()))
        self._portfolio_cache = (now, data)
        return data

    def get_equity(self) -> float:
        # Hyperliquid's portfolio endpoint is the authoritative total account
        # value (matches the UI). Summing marginSummary + spot USDC can double
        # count unified collateral, so use this first.
        now = time.time()
        cached = getattr(self, "_equity_cache", None)
        if cached and now - cached[0] < 5.0:
            return cached[1]
        try:
            port = self.get_portfolio()
            for period, data in port:
                if period in ("day", "week", "month") and isinstance(data, dict):
                    hist = data.get("accountValueHistory") or []
                    if hist:
                        val = float(hist[-1][1])
                        self._equity_cache = (now, val)
                        return val
        except Exception:
            pass

        total = 0.0
        try:
            state = self.get_user_state("")
            total += float(state.get("marginSummary", {}).get("accountValue", 0) or 0)
        except Exception:
            pass
        try:
            spot = _retry(lambda: self.info.spot_user_state(self.address()))
            for b in spot.get("balances", []):
                if b.get("coin") == "USDC":
                    total += float(b.get("total", 0) or 0)
        except Exception:
            pass
        if not self.cfg.perp_dexs:
            self._equity_cache = (now, total)
            return total
        for dex in self.cfg.perp_dexs:
            try:
                st = self.get_user_state(dex)
                total += float(st.get("marginSummary", {}).get("accountValue", 0) or 0)
            except Exception:
                pass
        self._equity_cache = (now, total)
        return total

    def get_positions_all(self) -> Dict[str, Dict[str, Any]]:
        """Return {coin: {side, size, entry, unrealized_pnl}} merged across dexs."""
        out: Dict[str, Dict[str, Any]] = {}
        for dex in self._dexs():
            try:
                state = self.get_user_state(dex)
            except Exception:
                continue
            for item in state.get("assetPositions", []):
                pos = item.get("position", {})
                coin = pos.get("coin")
                szi = float(pos["szi"])
                if abs(szi) < 1e-12:
                    continue
                side = "long" if szi > 0 else "short"
                out[coin] = {
                    "side": side,
                    "size": abs(szi),
                    "entry": float(pos.get("entryPx") or 0.0),
                    "unrealized_pnl": float(pos.get("unrealizedPnl") or 0.0),
                }
        return out

    def get_position(self, coin: str) -> Optional[Dict[str, Any]]:
        return self.get_positions_all().get(coin)

    # ----------------------------------------------------------------- orders
    def set_leverage(self, leverage: int, coin: str, is_cross: bool) -> Any:
        if self.exchange is None:
            raise RuntimeError("no exchange client in paper mode")
        return self.exchange.update_leverage(leverage, coin, is_cross)

    def market_open(self, coin: str, is_buy: bool, size: float, slippage: float) -> Dict[str, Any]:
        if self.exchange is None:
            raise RuntimeError("no exchange client in paper mode")
        return self.exchange.market_open(coin, is_buy, size, slippage=slippage)

    def market_close(self, coin: str, size: Optional[float], slippage: float) -> Dict[str, Any]:
        if self.exchange is None:
            raise RuntimeError("no exchange client in paper mode")
        return self.exchange.market_close(coin, size, slippage=slippage)

    def place_trigger(
        self, coin: str, is_buy: bool, size: float, trigger_px: float, tpsl: str
    ) -> Dict[str, Any]:
        """Place a reduce-only market trigger (TP/SL). Requires live mode.

        NOTE: verified against SDK source; verify on testnet before mainnet.
        """
        if self.exchange is None:
            raise RuntimeError("no exchange client in paper mode")
        order_type = {"trigger": {"isMarket": True, "triggerPx": trigger_px, "tpsl": tpsl}}
        return self.exchange.order(coin, is_buy, size, trigger_px, order_type, reduce_only=True)

    def get_open_orders(self) -> List[Dict[str, Any]]:
        return _retry(lambda: self.info.open_orders(self.address()))

    def cancel_open_orders(self, coin: str) -> None:
        if self.exchange is None:
            return
        for o in self.get_open_orders():
            if o.get("coin") == coin:
                self.exchange.cancel(coin, o["oid"])

    @staticmethod
    def ok(resp: Any) -> bool:
        return isinstance(resp, dict) and resp.get("status") == "ok"

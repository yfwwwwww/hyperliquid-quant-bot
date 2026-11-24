"""HF live execution layer.

Same signals as hf_bot, but orders go to the real Hyperliquid exchange:
  - entries: post-only (ALO) limit; cancel + optional taker on timeout
  - exits: TP1 closes 50% and moves stop to breakeven, TP2 closes the rest
  - stop / time stop close the remainder at market

State is persisted to state_hf_live.json so a restart keeps positions.

WARNING: untested against mainnet. Start with a small account and watch it.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

from .basis import basis_pct
from .client import HyperClient
from .config import Config
from .engines import evaluate
from .feed import RealtimeFeed
from .indicators import atr
from .journal import Journal
from .policy import load_policy, policy_ok
from .ledger import Ledger
from .mtf import direction_series
from .notify import make_notifier
from .orderflow import orderbook_imbalance, orderbook_walls
from .oi import OITracker
from .risk import round_size_down

log = logging.getLogger("hyperbot.hf.live")

STATE_PATH = "state_hf_live.json"


def _notify(cfg: Config, notifier, msg: str) -> None:
    # only critical messages bypass the trade-notification filter
    if cfg.notify_trades or msg.startswith(("⛔", "⚠️")):
        _notify(cfg, notifier, msg)


def _round_px(px: float) -> float:
    """Hyperliquid prices: max 5 significant figures (and <=6 decimals)."""
    if px <= 0:
        return px
    digits = 5 - int(math.floor(math.log10(abs(px)))) - 1
    return round(px, max(digits, 0))


class HFState:
    def __init__(self, path: str):
        self.path = path
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.day = time.strftime("%Y-%m-%d", time.gmtime())
        self.day_trades = 0
        self.day_start_equity = 0.0

    def save(self) -> None:
        data = {
            "positions": self.positions,
            "pending": self.pending,
            "day": self.day,
            "day_trades": self.day_trades,
            "day_start_equity": self.day_start_equity,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> "HFState":
        st = cls(path)
        if os.path.exists(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
                st.positions = d.get("positions") or {}
                st.pending = d.get("pending") or {}
                st.day = d.get("day") or st.day
                st.day_trades = int(d.get("day_trades") or 0)
                st.day_start_equity = float(d.get("day_start_equity") or 0.0)
            except Exception:
                log.warning("failed to load HF live state", exc_info=True)
        return st


def _portfolio_risk(st: HFState) -> float:
    total = sum(float(p.get("size", 0)) * abs(float(p.get("entry", 0)) - float(p.get("stop", 0)))
                for p in st.positions.values())
    eq = _equity_hint.get("v", 0.0)
    return total / eq if eq > 0 else 1.0


_equity_hint: Dict[str, float] = {"v": 0.0}
_journal: Optional[Journal] = None
_oi = OITracker()


def _ctx(coin: str, basis_v, cvd, imb, client: HyperClient) -> Dict[str, Any]:
    ctx = client.asset_ctx(coin)
    oi = float(ctx.get("openInterest", 0) or 0)
    if oi > 0:
        _oi.update(coin, oi)
    walls = orderbook_walls(client, coin, 50)
    return {
        "basis": basis_v, "cvd": cvd, "imb": imb,
        "funding_apr": float(ctx.get("funding", 0) or 0) * 24 * 365 * 100.0,
        "spread_bps": client.get_spread(coin),
        "wall_imb": walls.get("wall_imb") if walls else None,
        "oi_change_1h": _oi.change(coin, 3600),
    }
_missing: Dict[str, int] = {}
_orphan: Dict[str, int] = {}


def _open_orders(client: HyperClient) -> List[Dict[str, Any]]:
    try:
        return client.info.open_orders(client.address())
    except Exception:
        return []


def _market_open(client: HyperClient, coin: str, side: str, size: float) -> bool:
    try:
        resp = client.market_open(coin, side == "long", size, client.cfg.slippage)
        return client.ok(resp)
    except Exception:
        log.exception("market_open failed %s", coin)
        return False


def _market_close(client: HyperClient, coin: str, size: float) -> bool:
    if size <= 0:
        return True
    try:
        resp = client.market_close(coin, size, client.cfg.slippage)
        return client.ok(resp)
    except Exception:
        log.exception("market_close failed %s", coin)
        return False


def _place_sl(client: HyperClient, coin: str, side: str, size: float, stop: float) -> Optional[int]:
    if size <= 0 or stop <= 0 or client.exchange is None:
        return None
    close_is_buy = side != "long"
    try:
        resp = client.place_trigger(coin, close_is_buy, size, _round_px(stop), "sl")
        if not client.ok(resp):
            log.warning("SL rejected %s: %s", coin, resp)
            return None
        st = resp["response"]["data"]["statuses"][0]
        if "resting" in st:
            return int(st["resting"]["oid"])
        if "filled" in st:
            return int(st["filled"]["oid"])
    except Exception:
        log.exception("SL failed %s", coin)
    return None


def _cancel_sl(client: HyperClient, coin: str, oid: Any) -> None:
    if not oid or client.exchange is None:
        return
    try:
        client.exchange.cancel(coin, int(oid))
    except Exception:
        pass


def _attach_sl(client: HyperClient, st: "HFState", coin: str) -> None:
    p = st.positions.get(coin)
    if not p or p.get("sl_oid"):
        return
    oid = _place_sl(client, coin, p["side"], float(p["size"]), float(p["stop"]))
    if oid:
        p["sl_oid"] = oid


def _close_all(cfg: Config, client: HyperClient, st: "HFState", coin: str, mark: float,
               reason: str, ledger: Ledger, notifier) -> None:
    p = st.positions.get(coin)
    if not p:
        return
    _cancel_sl(client, coin, p.get("sl_oid"))
    size = float(p["size"])
    if _market_close(client, coin, size):
        sign = 1 if p["side"] == "long" else -1
        pnl = (mark - float(p["entry"])) * size * sign - mark * size * cfg.fee_rate
        st.positions.pop(coin, None)
        log.info("HF close %s %s %s size=%s mark=%s pnl=%.2f", coin, p["side"], reason, size, mark, pnl)
        if _journal is not None:
            _journal.record("close", coin, p["side"], mark, size, pnl=pnl, reason=reason,
                            context=p.get("ctx") or {})
        ledger.log_trade(coin, p["side"], "close", mark, size, reason=reason)
        _notify(cfg, notifier, f"🔴 HF实盘平仓 {coin} {p['side']} {reason} @ {mark:.6g}")


def _place_alo(client: HyperClient, coin: str, side: str, size: float, limit: float) -> Optional[int]:
    try:
        resp = client.exchange.order(
            coin, side == "long", size, limit, {"limit": {"tif": "Alo"}}, reduce_only=False
        )
        if not client.ok(resp):
            log.warning("ALO rejected %s: %s", coin, resp)
            return None
        st = resp["response"]["data"]["statuses"][0]
        if "resting" in st:
            return int(st["resting"]["oid"])
        if "filled" in st:
            return -1
    except Exception:
        log.exception("ALO failed %s", coin)
    return None


def _manage(cfg: Config, client: HyperClient, st: HFState, coin: str, mark: float,
            ledger: Ledger, notifier) -> None:
    p = st.positions[coin]
    p["bars"] = int(p.get("bars", 0)) + 1
    side = p["side"]
    size = float(p["size"])
    if size <= 0:
        st.positions.pop(coin, None)
        return
    if side == "long":
        if mark <= float(p["stop"]):
            _close_all(cfg, client, st, coin, mark, "stop", ledger, notifier)
        elif not p.get("tp1_taken") and mark >= float(p["tp1"]):
            part = round_size_down(size * cfg.scalp_tp1_frac, client.sz_decimals(coin))
            if part <= 0:
                part = size
            if _market_close(client, coin, part):
                p["size"] = size - part
                p["tp1_taken"] = True
                p["stop"] = float(p["entry"])
                _cancel_sl(client, coin, p.pop("sl_oid", None))
                if float(p["size"]) > 0:
                    _attach_sl(client, st, coin)
                ledger.log_trade(coin, side, "close", mark, part, reason="tp1")
                _notify(cfg, notifier, f"🟢 HF实盘TP1 {coin} 平{part}，止损已移到保本并重挂")
        elif p.get("tp1_taken") and mark >= float(p["tp2"]):
            _close_all(cfg, client, st, coin, mark, "tp2", ledger, notifier)
    else:
        if mark >= float(p["stop"]):
            _close_all(cfg, client, st, coin, mark, "stop", ledger, notifier)
        elif not p.get("tp1_taken") and mark <= float(p["tp1"]):
            part = round_size_down(size * cfg.scalp_tp1_frac, client.sz_decimals(coin))
            if part <= 0:
                part = size
            if _market_close(client, coin, part):
                p["size"] = size - part
                p["tp1_taken"] = True
                p["stop"] = float(p["entry"])
                _cancel_sl(client, coin, p.pop("sl_oid", None))
                if float(p["size"]) > 0:
                    _attach_sl(client, st, coin)
                ledger.log_trade(coin, side, "close", mark, part, reason="tp1")
                _notify(cfg, notifier, f"🟢 HF实盘TP1 {coin} 平{part}，止损已移到保本并重挂")
        elif p.get("tp1_taken") and mark <= float(p["tp2"]):
            _close_all(cfg, client, st, coin, mark, "tp2", ledger, notifier)
    if coin in st.positions and int(p.get("bars", 0)) >= cfg.scalp_max_bars:
        _close_all(cfg, client, st, coin, mark, "time", ledger, notifier)
    # self-heal: every tick, make sure the position still has an exchange stop
    if coin in st.positions and not st.positions[coin].get("sl_oid"):
        _attach_sl(client, st, coin)


def _adopt(cfg: Config, client: HyperClient, st: "HFState", coin: str, api: Dict[str, Any], notifier) -> None:
    """Adopt an exchange position that is missing from state instead of closing it."""
    try:
        side = api["side"]
        size = float(api["size"])
        entry = float(api["entry"] or 0.0)
        mark = client.get_mid(coin)
        cs = client.get_candles(coin, cfg.scalp_interval, 200)
        a = atr(cs, cfg.atr_period)[-1]
    except Exception:
        log.exception("adopt failed for %s", coin)
        return
    if size <= 0 or entry <= 0 or a <= 0:
        return
    sign = 1 if side == "long" else -1
    risk_dist = 2.0 * a
    st.positions[coin] = {
        "side": side, "size": size, "entry": entry,
        "stop": entry - sign * risk_dist,
        "tp1": entry + sign * cfg.scalp_r_mult1 * risk_dist,
        "tp2": entry + sign * cfg.scalp_r_mult2 * risk_dist,
        "tp1_taken": False, "bars": 0,
    }
    _attach_sl(client, st, coin)
    log.warning("adopted untracked position %s %s size=%s entry=%s", coin, side, size, entry)
    _notify(cfg, notifier, f"⚠️ 收养未记录仓位 {coin} {side} {size}，已补挂止损")


def _tick(cfg: Config, client: HyperClient, feed: Optional[RealtimeFeed], ledger: Ledger,
          notifier, st: HFState, coin: str) -> None:
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if st.day != today:
        st.day = today
        st.day_trades = 0

    candles = client.get_candles(coin, cfg.scalp_interval, max(cfg.candle_count, 300))
    if len(candles) < 60:
        return
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    i = len(candles) - 1
    atr_v = atr(candles, cfg.atr_period)[i]
    try:
        mark = client.get_mid(coin)
    except Exception:
        mark = closes[i]

    # reconcile with the exchange. If the API fails, skip this tick entirely:
    # acting on a bad snapshot could close real positions.
    try:
        api_all = client.get_positions_all()
    except Exception:
        log.warning("position sync failed; skipping tick")
        return
    for c in list(st.positions):
        p = api_all.get(c)
        if p and p.get("size", 0) > 0:
            _missing[c] = 0
            continue
        _missing[c] = _missing.get(c, 0) + 1
        if _missing[c] >= 2:
            st.positions.pop(c, None)
            _missing[c] = 0
    for c, p in api_all.items():
        if c in cfg.coins and p.get("size", 0) > 0 and c not in st.positions and c not in st.pending:
            _orphan[c] = _orphan.get(c, 0) + 1
            if _orphan[c] >= 2:
                _adopt(cfg, client, st, c, p, notifier)
                _orphan[c] = 0

    # hard floor
    try:
        eq = client.get_equity()
    except Exception:
        eq = 0.0
    _equity_hint["v"] = eq
    if st.day_start_equity <= 0:
        st.day_start_equity = eq
    if cfg.min_equity_stop > 0 and 0 < eq <= cfg.min_equity_stop:
        for c in list(st.positions):
            _cancel_sl(client, c, st.positions[c].get("sl_oid"))
            _market_close(client, c, float(st.positions[c].get("size", 0)))
            st.positions.pop(c, None)
        for c, p in list(st.pending.items()):
            try:
                client.exchange.cancel(c, int(p["oid"]))
            except Exception:
                pass
            st.pending.pop(c, None)
        st.save()
        _notify(cfg, notifier, f"⛔ HF实盘硬地板：权益 {eq:.2f}，已全平停机")
        return

    if coin in st.positions:
        _manage(cfg, client, st, coin, mark, ledger, notifier)
        st.save()
        return

    # pending maker order
    pending = st.pending.get(coin)
    if pending is not None:
        oid = int(pending["oid"])
        resting = {int(o.get("oid", 0)) for o in _open_orders(client)}
        if oid in resting:
            if time.time() - float(pending["ts"]) > cfg.maker_timeout_seconds:
                try:
                    client.exchange.cancel(coin, oid)
                except Exception:
                    pass
                st.pending.pop(coin, None)
                if cfg.maker_fallback_taker and _market_open(client, coin, pending["side"], float(pending["size"])):
                    st.positions[coin] = {
                        "side": pending["side"], "size": float(pending["size"]), "entry": mark,
                        "stop": float(pending["stop"]), "tp1": float(pending["tp1"]),
                        "tp2": float(pending["tp2"]), "tp1_taken": False, "bars": 0,
                    }
                    ledger.log_trade(coin, pending["side"], "open", mark, float(pending["size"]), reason="hf_taker")
                    _notify(cfg, notifier, f"🟡 HF实盘挂单超时，市价补单 {coin} {pending['side']}")
            st.save()
            return

        # oid no longer resting: it either filled or was cancelled
        api = api_all.get(coin)
        if api and api.get("size", 0) > 0:
            st.positions[coin] = {
                "side": pending["side"], "size": float(api["size"]),
                "entry": float(api["entry"] or pending["limit"]),
                "stop": float(pending["stop"]), "tp1": float(pending["tp1"]),
                "tp2": float(pending["tp2"]), "tp1_taken": False, "bars": 0,
            }
            ledger.log_trade(coin, pending["side"], "open", float(api["entry"] or pending["limit"]),
                             float(api["size"]), reason="hf_maker")
            log.info("HF maker filled %s %s size=%s entry=%s", coin, pending["side"], api["size"], api["entry"])
            if _journal is not None:
                _journal.record("open", coin, pending["side"], float(api["entry"]), float(api["size"]),
                                reason="maker", context=pending.get("ctx") or {})
            _notify(cfg, notifier, f"🟢 HF实盘挂单成交 {coin} {pending['side']} @ {api['entry']}")
            if len(st.positions) > cfg.max_positions:
                _market_close(client, coin, float(api["size"]))
                st.positions.pop(coin, None)
                _notify(cfg, notifier, f"⚠️ 超过最大持仓 {cfg.max_positions}，已平掉新仓 {coin}")
            st.pending.pop(coin, None)
        else:
            # fill/position data may lag; wait 2 ticks before treating as cancelled
            pending["gone"] = int(pending.get("gone", 0)) + 1
            if pending["gone"] >= 2:
                st.pending.pop(coin, None)
                if cfg.maker_fallback_taker and _market_open(client, coin, pending["side"], float(pending["size"])):
                    st.positions[coin] = {
                        "side": pending["side"], "size": float(pending["size"]), "entry": mark,
                        "stop": float(pending["stop"]), "tp1": float(pending["tp1"]),
                        "tp2": float(pending["tp2"]), "tp1_taken": False, "bars": 0,
                    }
                    ledger.log_trade(coin, pending["side"], "open", mark, float(pending["size"]), reason="hf_taker")
                    _notify(cfg, notifier, f"🟡 HF实盘挂单未成交，市价补单 {coin} {pending['side']}")
        st.save()
        return

    if len(st.positions) + len(st.pending) >= cfg.max_positions:
        return
    if cfg.max_trades_per_day > 0 and st.day_trades >= cfg.max_trades_per_day:
        return

    mtf_dirs = []
    for tf in ("15m", "1h", "4h"):
        try:
            hc = client.get_candles(coin, tf, 200)
            mtf_dirs.append(direction_series([x["c"] for x in hc], 20, 50)[-1])
        except Exception:
            mtf_dirs.append(0)
    cvd = feed.taker_delta(coin, cfg.scalp_cvd_window) if feed is not None else None
    imb = orderbook_imbalance(client, coin, cfg.orderbook_levels)
    basis_v = basis_pct(coin, mark)
    policy = load_policy()
    spread_bps = client.get_spread(coin)
    if not policy_ok(policy, {"imb": imb, "spread_bps": spread_bps, "basis": basis_v,
                              "cvd": cvd, "funding_apr": None}):
        return
    sigs = evaluate(coin, candles, mtf_dirs, cvd, imb, basis_v)
    if not sigs:
        return
    sig = max(sigs, key=lambda x: x.score)
    side = sig.side
    stop_mult = 3.0 if sig.engine == "A" else 2.0
    stop_dist = stop_mult * atr_v
    if stop_dist <= 0 or mark <= 0 or eq <= 0:
        return
    raw = (eq * cfg.risk_per_trade) / stop_dist
    raw = min(raw, (eq * cfg.leverage * cfg.max_exposure) / mark)
    size = round_size_down(raw, client.sz_decimals(coin))
    if size <= 0 or size * mark < cfg.min_notional:
        return
    sign = 1 if side == "long" else -1
    stop = mark - sign * stop_dist
    tp1 = mark + sign * cfg.scalp_r_mult1 * stop_dist
    tp2 = mark + sign * cfg.scalp_r_mult2 * stop_dist
    if _portfolio_risk(st) + (size * stop_dist) / eq > cfg.portfolio_risk_cap_pct:
        return

    try:
        api = client.get_positions_all().get(coin)
    except Exception:
        api = None
    if api and api.get("size", 0) > 0:
        return

    st.day_trades += 1
    if cfg.use_maker:
        limit = _round_px(mark - sign * cfg.maker_offset_atr * atr_v)
        oid = _place_alo(client, coin, side, size, limit)
        if oid is not None:
            log.info("HF maker order %s %s size=%s limit=%s", coin, side, size, limit)
            st.pending[coin] = {
                "oid": oid, "side": side, "size": size, "limit": limit,
                "stop": stop, "tp1": tp1, "tp2": tp2, "ts": time.time(),
                "ctx": _ctx(coin, basis_v, cvd, imb, client),
            }
            _notify(cfg, notifier, f"📌 HF实盘挂单 {coin} {side} {size} @ {limit:.6g}")
            st.save()
            return
    if _market_open(client, coin, side, size):
        st.positions[coin] = {
            "side": side, "size": size, "entry": mark,
            "stop": stop, "tp1": tp1, "tp2": tp2,
            "tp1_taken": False, "bars": 0,
        }
        _attach_sl(client, st, coin)
        log.info("HF taker open %s %s size=%s entry=%s stop=%s", coin, side, size, mark, stop)
        if _journal is not None:
            _journal.record("open", coin, side, mark, size, reason="taker",
                            context=_ctx(coin, basis_v, cvd, imb, client))
        ledger.log_trade(coin, side, "open", mark, size, reason="hf_taker")
        _notify(cfg, notifier, f"🟢 HF实盘市价开仓 {coin} {side} @ {mark:.6g}")
    st.save()


def run_hf_live(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)
    client = HyperClient(cfg)
    feed = RealtimeFeed(cfg, client.base_url) if cfg.use_websocket else None
    if feed is not None:
        feed.start(cfg.coins)
    global _journal
    _journal = Journal(cfg.journal_file)
    st = HFState.load(STATE_PATH)
    ledger = Ledger(cfg.ledger_file)
    notifier = make_notifier(cfg)

    # safety: close any exchange position we are not tracking (e.g. after a crash)
    try:
        for coin, p in client.get_positions_all().items():
            if p.get("size", 0) > 0 and coin not in st.positions:
                _adopt(cfg, client, st, coin, p, notifier)
    except Exception:
        log.exception("startup position sync failed")
    for _c in list(st.positions):
        _attach_sl(client, st, _c)
    log.info("HF LIVE started: coins=%s tf=%s risk=%.2f%% lev=%s floor=%.2f",
             cfg.coins, cfg.scalp_interval, cfg.risk_per_trade * 100, cfg.leverage, cfg.min_equity_stop)
    _notify(cfg, notifier, f"🔴 HF 实盘启动 | {cfg.coins} {cfg.scalp_interval} | 风险 {cfg.risk_per_trade*100:.1f}%")
    last_summary = 0.0
    try:
        while True:
            try:
                for coin in cfg.coins:
                    _tick(cfg, client, feed, ledger, notifier, st, coin)
                st.save()
                if time.time() - last_summary >= cfg.notify_interval_seconds:
                    last_summary = time.time()
                    try:
                        eq_now = client.get_equity()
                        mids = client.get_mids_all()
                        floating = 0.0
                        parts = []
                        for c, p in st.positions.items():
                            mark = mids.get(c, float(p["entry"]))
                            sign = 1 if p["side"] == "long" else -1
                            upnl = (mark - float(p["entry"])) * float(p["size"]) * sign
                            floating += upnl
                            parts.append(f"{c} {p['side']} {upnl:+.2f}")
                        day_pnl = eq_now - st.day_start_equity if st.day_start_equity > 0 else 0.0
                        msg = (f"📊 HF 2h | 权益 ${eq_now:.2f} | 今日 {day_pnl:+.2f} "
                               f"| 浮动 {floating:+.2f} | 持仓 {len(st.positions)} | 今日 {st.day_trades} 单")
                        if parts:
                            msg += "\n" + " | ".join(parts)
                        notifier.send(msg)
                    except Exception:
                        log.exception("summary failed")
                try:
                    ledger.log_equity(client.get_equity(), "mainnet")
                except Exception:
                    pass
            except KeyboardInterrupt:
                st.save()
                break
            except Exception:
                log.exception("hf live tick failed")
            time.sleep(cfg.scalp_poll_seconds)
    finally:
        if feed is not None:
            try:
                feed.stop()
            except Exception:
                pass

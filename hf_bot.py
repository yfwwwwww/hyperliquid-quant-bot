"""Multi-signal high-frequency paper bot.

Signals on the base timeframe (default 5m):
  - EMA(20/50) trend direction
  - Donchian(20) breakout
  - CVD / taker delta (websocket trades)
  - L2 orderbook imbalance
  - MTF gate: >=2 of 15m/1h/4h EMA(20/50) agree with the side

Exit: TP1 50% -> breakeven -> TP2, ATR stop, max-bars time stop.
Paper only. Reuses SPos/ScalpState/_manage from scalp_paper.
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Optional

from .client import HyperClient
from .config import Config
from .basis import basis_pct
from .engines import evaluate
from .journal import Journal
from .execution import PendingOrder, maker_entry_fee, maker_limit_price, taker_entry_fee, touched, expired
from .feed import RealtimeFeed
from .indicators import atr, ema
from .ledger import Ledger
from .mtf import direction_series
from .notify import make_notifier
from .orderflow import orderbook_imbalance, orderbook_walls
from .oi import OITracker
from .risk import round_size_down
from .scalp_paper import SPos, ScalpState, _manage

log = logging.getLogger("hyperbot.hf")

MAX_POSITIONS = 3
_pending: dict[str, PendingOrder] = {}
_cooldown: dict[str, float] = {}
_day_trades: dict[str, object] = {"day": "", "n": 0}
_journal: Optional[Journal] = None
_entry_ctx: dict = {}
_oi = OITracker()


def _portfolio_risk(st: ScalpState) -> float:
    total = sum(p.remaining * abs(p.entry - p.stop) for p in st.positions.values())
    return total / st.equity if st.equity > 0 else 1.0
DONCHIAN_N = 20


def _open_position(cfg, st, ledger, notifier, coin, side, size, price, stop, tp1, tp2, taker):
    fee = taker_entry_fee(cfg, price, size) if taker else maker_entry_fee(cfg, price, size)
    st.equity -= fee
    pos = SPos(
        side=side, size=size, remaining=size, entry=price,
        stop=stop, tp1=tp1, tp2=tp2, entry_t=time.time(),
    )
    st.positions[coin] = pos
    ledger.log_trade(coin, side, "open", price, size, reason="hf_taker" if taker else "hf_maker")
    notifier.send(
        f"🟢 HF{'市价' if taker else '挂单'}成交 {coin} {side} @ {price:.6g} | size={size} "
        f"| stop={stop:.6g} TP1={tp1:.6g}"
    )


def _mtf_ok(cfg: Config, client: HyperClient, coin: str, side: str) -> bool:
    agree = 0
    for tf in ("15m", "1h", "4h"):
        try:
            cs = client.get_candles(coin, tf, 200)
            d = direction_series([c["c"] for c in cs], 20, 50)[-1]
            if (side == "long" and d > 0) or (side == "short" and d < 0):
                agree += 1
        except Exception:
            pass
    return agree >= cfg.hf_mtf_min


def _tick(cfg: Config, client: HyperClient, feed: Optional[RealtimeFeed], ledger: Ledger,
          notifier, st: ScalpState, coin: str) -> None:
    st.ensure_day()
    candles = client.get_candles(coin, cfg.scalp_interval, max(cfg.candle_count, 300))
    if len(candles) < 60:
        return
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    i = len(candles) - 1
    atr_v = atr(candles, cfg.atr_period)[i]
    ema_f = ema(closes, 20)[i]
    ema_s = ema(closes, 50)[i]
    up = max(highs[i - DONCHIAN_N : i])
    dn = min(lows[i - DONCHIAN_N : i])
    mark = closes[i]
    if feed is not None:
        p = feed.latest_price(coin)
        if p:
            mark = p

    pos = st.positions.get(coin)
    if pos is not None:
        _manage(cfg, st, pos, coin, mark, ledger, notifier)
        if coin not in st.positions:
            ctx = _entry_ctx.pop(coin, {})
            if _journal is not None:
                _journal.record("close", coin, pos.side, mark, pos.size, pnl=pos.pnl,
                                reason="close", context=ctx)
            _cooldown[coin] = time.time() + cfg.hf_cooldown_seconds
        return

    if cfg.min_equity_stop > 0 and st.equity <= cfg.min_equity_stop:
        _pending.pop(coin, None)
        return
    if st.halted(cfg):
        _pending.pop(coin, None)
        return

    pending = _pending.get(coin)
    if pending is not None:
        if touched(pending, mark):
            _pending.pop(coin, None)
            _open_position(cfg, st, ledger, notifier, coin, pending.side, pending.qty,
                           pending.limit, pending.stop, pending.tp1, pending.tp2, taker=False)
            return
        if expired(pending, cfg.maker_timeout_seconds):
            _pending.pop(coin, None)
            if cfg.maker_fallback_taker:
                _open_position(cfg, st, ledger, notifier, coin, pending.side, pending.qty,
                               mark, pending.stop, pending.tp1, pending.tp2, taker=True)
            return
        return

    if len(st.positions) >= cfg.max_positions:
        return
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _day_trades.get("day") != today:
        _day_trades["day"] = today
        _day_trades["n"] = 0
    if cfg.max_trades_per_day > 0 and int(_day_trades["n"]) >= cfg.max_trades_per_day:
        return
    if time.time() < _cooldown.get(coin, 0):
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
    sigs = evaluate(coin, candles, mtf_dirs, cvd, imb, basis_v, cfg.hf_min_score, cfg.hf_mtf_min)
    if not sigs:
        return
    sig = max(sigs, key=lambda x: x.score)
    side = sig.side
    stop_mult = 3.0 if sig.engine == "A" else 2.0

    stop_dist = stop_mult * atr_v
    if stop_dist <= 0 or mark <= 0 or st.equity <= 0:
        return
    raw = (st.equity * cfg.risk_per_trade) / stop_dist
    raw = min(raw, (st.equity * cfg.leverage * cfg.max_exposure) / mark)
    size = round_size_down(raw, client.sz_decimals(coin))
    if size <= 0 or size * mark < cfg.min_notional:
        return
    if _portfolio_risk(st) + (size * stop_dist) / st.equity > cfg.portfolio_risk_cap_pct:
        return
    _day_trades["n"] = int(_day_trades["n"]) + 1

    sign = 1 if side == "long" else -1
    stop = mark - sign * stop_dist
    tp1 = mark + sign * cfg.scalp_r_mult1 * stop_dist
    tp2 = mark + sign * cfg.scalp_r_mult2 * stop_dist
    if cfg.use_maker:
        limit = maker_limit_price(side, mark, atr_v, cfg.maker_offset_atr)
        _pending[coin] = PendingOrder(coin, side, size, limit, time.time(), stop, tp1, tp2, atr_v)
        log.info("HF maker order coin=%s %s qty=%s limit=%.6g engine=%s score=%d", coin, side, size, limit, sig.engine, sig.score)
        return
    _open_position(cfg, st, ledger, notifier, coin, side, size, mark, stop, tp1, tp2, taker=True)
    actx = client.asset_ctx(coin)
    aoi = float(actx.get("openInterest", 0) or 0)
    if aoi > 0:
        _oi.update(coin, aoi)
    walls = orderbook_walls(client, coin, 50)
    _entry_ctx[coin] = {
        "basis": basis_v, "cvd": cvd, "imb": imb,
        "funding_apr": float(actx.get("funding", 0) or 0) * 24 * 365 * 100.0,
        "spread_bps": client.get_spread(coin),
        "wall_imb": walls.get("wall_imb") if walls else None,
        "oi_change_1h": _oi.change(coin, 3600),
    }
    if _journal is not None:
        _journal.record("open", coin, side, mark, size, reason="hf_taker", context=_entry_ctx[coin])


def run_hf_paper(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    feed = RealtimeFeed(cfg, client.base_url) if cfg.use_websocket else None
    if feed is not None:
        feed.start(cfg.coins)
    global _journal
    _journal = Journal(cfg.journal_file)
    state_path = cfg.state_file.replace(".json", "_hf.json")
    st = ScalpState.load(state_path, cfg.paper_start_equity)
    ledger = Ledger(cfg.ledger_file)
    notifier = make_notifier(cfg)

    log.info(
        "HF paper started: coins=%s tf=%s cvd=%s feed=%s equity=%.2f",
        cfg.coins, cfg.scalp_interval, cfg.scalp_require_cvd, feed is not None, st.equity,
    )
    notifier.send(f"🚀 HF paper 启动 | {cfg.coins} {cfg.scalp_interval}")

    last_summary = 0.0
    try:
        while True:
            try:
                for coin in cfg.coins:
                    _tick(cfg, client, feed, ledger, notifier, st, coin)
                st.save()
                ledger.log_equity(st.equity, cfg.mode)
                if time.time() - last_summary >= 3600:
                    last_summary = time.time()
                    notifier.send(
                        f"📊 HF paper | 权益 ${st.equity:.2f} | 今日 {int(_day_trades['n'])} 单 "
                        f"| 持仓 {len(st.positions)} | 组合风险 {_portfolio_risk(st)*100:.1f}%"
                    )
            except KeyboardInterrupt:
                st.save()
                break
            except Exception:
                log.exception("hf tick failed; retry next poll")
            time.sleep(cfg.scalp_poll_seconds)
    finally:
        if feed is not None:
            try:
                feed.stop()
            except Exception:
                pass

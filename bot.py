"""Main bot loop: fetch candles, decide, manage positions, enforce risk limits.

Order of entry gates: EMA cross -> strategy filters -> MTF resonance -> MACD ->
order flow -> position sizing. Notifications fire on entry/exit/halt.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Set

from .account import LiveAccount, PaperAccount, Position, State
from .client import HyperClient
from .config import Config
from .feed import RealtimeFeed
from .ledger import Ledger
from .mtf import MultiTimeframe
from .notify import Notifier, make_notifier
from .orderflow import allows, read_orderflow
from .report import daily_summary
from .risk import compute_position_size
from .strategy import (
    compute_indicators,
    long_ok,
    short_ok,
    stop_and_target,
    stop_hit,
)

log = logging.getLogger("hyperbot")
_halt_notified: Set[str] = set()
_floor_notified: Set[str] = set()
_floor_strikes: Dict[str, int] = {}


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@dataclass
class Context:
    cfg: Config
    client: HyperClient
    account: object
    state: State
    feed: Optional[RealtimeFeed]
    notifier: Notifier
    mtf: Optional[MultiTimeframe]
    ledger: Ledger


def _build_account(cfg: Config, state: State, client: HyperClient):
    if cfg.mode == "paper":
        return PaperAccount(state, cfg)
    account = LiveAccount(state, client)
    for coin in cfg.coins:
        client.set_leverage(cfg.leverage, coin, cfg.is_cross)
    account.sync_positions()
    return account


def _total_equity(cfg: Config, account, client: HyperClient, mids: Dict[str, float], coin: str, mark: float) -> float:
    if cfg.mode != "paper":
        return client.get_equity()
    total = account.realized_equity()
    for c, pos in account.positions().items():
        if pos.is_open():
            m = mark if c == coin else mids.get(c, pos.entry)
            total += account.unrealized_pnl(c, m)
    return total


def tick(ctx: Context, coin: str) -> None:
    cfg = ctx.cfg
    client = ctx.client
    account = ctx.account
    state = ctx.state

    funding = client.get_funding(coin) if cfg.funding_filter else None
    candles = client.get_candles(coin, cfg.interval, cfg.candle_count)
    snap = compute_indicators(candles, cfg, funding=funding)

    mids = client.get_mids_all()
    mark = mids.get(coin, snap.close)
    if ctx.feed is not None:
        live = ctx.feed.latest_price(coin)
        if live:
            mark = live

    state.ensure_day()
    total_equity = _total_equity(cfg, account, client, mids, coin, mark)
    if state.day_start_equity <= 0:
        state.day_start_equity = total_equity
    halted = state.daily_halted(total_equity, cfg)
    ctx.ledger.log_equity(total_equity, cfg.mode)

    if cfg.mode != "paper":
        account.sync_positions()
    pos = account.position(coin)
    sz_dec = client.sz_decimals(coin)

    # hard equity floor: require N consecutive confirmations before halting
    at_floor = cfg.min_equity_stop > 0 and total_equity <= cfg.min_equity_stop
    if at_floor:
        _floor_strikes[coin] = _floor_strikes.get(coin, 0) + 1
        if _floor_strikes[coin] >= cfg.floor_confirm_ticks:
            if coin not in _floor_notified:
                _floor_notified.add(coin)
                log.critical(
                    "FLOOR coin=%s equity=%.2f <= %.2f; halting",
                    coin, total_equity, cfg.min_equity_stop,
                )
                ctx.notifier.send(
                    f"⛔ 硬地板触发：权益 {total_equity:.2f} ≤ {cfg.min_equity_stop:.2f}，已停止交易"
                )
            if pos.is_open():
                pnl = account.close(coin, mark)
                log.info("FLOOR FLAT coin=%s pnl=%.4f", coin, pnl)
                ctx.notifier.send(f"⛔ 硬地板：已强制平仓 {coin}")
            state.save()
            return
        log.warning(
            "FLOOR suspected coin=%s equity=%.2f <= %.2f (%d/%d); no new entries",
            coin, total_equity, cfg.min_equity_stop,
            _floor_strikes[coin], cfg.floor_confirm_ticks,
        )
    else:
        _floor_strikes[coin] = 0

    # ----------------------------------------------------------------- manage
    if pos.is_open():
        if pos.stop is None and pos.entry and pos.entry > 0:
            stop, target = stop_and_target(pos.side, pos.entry, snap.atr, cfg)
            pos.stop, pos.target = stop, target
            if cfg.mode != "paper":
                account.set_stop_target(coin, stop, target)

        reason: Optional[str] = None
        if halted:
            reason = "daily-halt"
        elif stop_hit(pos.side, mark, pos.stop, pos.target):
            reason = "stop/target"
        elif pos.side == "long" and snap.cross_down:
            reason = "cross"
        elif pos.side == "short" and snap.cross_up:
            reason = "cross"

        if reason:
            pnl = account.close(coin, mark)
            log.info("EXIT %s coin=%s side=%s size=%s pnl=%.4f", reason, coin, pos.side, pos.size, pnl)
            ctx.notifier.send(
                f"🔴 平仓 {coin} {pos.side} | 原因={reason} | PnL={pnl:+.2f}"
            )
            ctx.ledger.log_trade(coin, pos.side, "close", mark, pos.size, pnl=pnl, reason=reason)
            if reason == "stop/target":
                state.set_cooldown(coin, cfg.cooldown_seconds)
        else:
            log.info(
                "HOLD coin=%s side=%s entry=%.6g mark=%.6g stop=%.6g atr=%.6g adx=%s roc=%s funding=%s",
                coin, pos.side, pos.entry, mark, pos.stop or 0.0, snap.atr,
                "%.1f" % snap.adx if snap.adx is not None else "-",
                "%.4f" % snap.roc if snap.roc is not None else "-",
                "%.5f" % snap.funding if snap.funding is not None else "-",
            )
        state.save()
        return

    # ------------------------------------------------------------------- entry
    if halted or at_floor:
        if coin not in _halt_notified:
            _halt_notified.add(coin)
            log.warning("HALTED coin=%s: daily loss limit reached (equity=%.2f).", coin, total_equity)
            ctx.notifier.send(f"⛔ 熔断 {coin}：单日亏损达上限，停止开仓（权益 {total_equity:.2f}）")
        state.save()
        return

    if state.in_cooldown(coin):
        log.info("COOLDOWN coin=%s: %.0fs left.", coin, state.cooldown_until.get(coin, 0) - time.time())
        state.save()
        return

    if account.open_count() >= cfg.max_positions:
        log.info("CAP coin=%s: max_positions (%d) reached; skipping new entry.", coin, cfg.max_positions)
        state.save()
        return

    if snap.cross_up and long_ok(snap, cfg):
        side = "long"
    elif snap.cross_down and short_ok(snap, cfg):
        side = "short"
    else:
        state.save()
        return

    # --- multi-timeframe resonance gate (fetches extra TFs only on a signal)
    mtf = ctx.mtf
    mtf_agree: Optional[float] = None
    if cfg.use_mtf_filter and mtf is not None:
        mtf_res = mtf.evaluate(coin, side)
        mtf_agree = mtf_res.agree
        if mtf_agree < cfg.mtf_min_agree:
            log.info("MTF veto coin=%s side=%s agree=%.2f < %.2f", coin, side, mtf_agree, cfg.mtf_min_agree)
            state.save()
            return
        if cfg.use_macd_filter and not mtf_res.macd_agrees(side):
            log.info("MACD veto coin=%s side=%s", coin, side)
            state.save()
            return

    # --- order-flow gate
    if cfg.use_orderflow_filter:
        of = read_orderflow(client, ctx.feed, coin, cfg)
        if not allows(of, side, cfg):
            log.info("ORDERFLOW veto coin=%s side=%s imb=%s taker=%s", coin, side, of.imbalance, of.taker_buy_frac)
            state.save()
            return

    equity = account.realized_equity() if cfg.mode == "paper" else total_equity
    size = compute_position_size(equity, mark, snap.atr, sz_dec, cfg)
    if size is None:
        log.info("SIGNAL %s coin=%s but size too small (equity=%.2f, atr=%.6g); skipping.", side, coin, equity, snap.atr)
        state.save()
        return

    stop, target = stop_and_target(side, mark, snap.atr, cfg)
    account.open(coin, side, size, mark, stop, target)
    log.info("ENTRY %s coin=%s size=%s @~%.6g stop=%.6g target=%s equity=%.2f", side, coin, size, mark, stop, target, equity)
    extra = f" | MTF共振={mtf_agree * 100:.0f}%" if mtf_agree is not None else ""
    ctx.notifier.send(
        f"🟢 开仓 {coin} {side} | 价格={mark:.6g} | 仓位={size} | 止损={stop:.6g}{extra}"
    )
    ctx.ledger.log_trade(coin, side, "open", mark, size, reason="entry")
    state.save()


def run_bot(cfg: Config) -> None:
    setup_logging(cfg.log_level)
    client = HyperClient(cfg)
    state = State.load(cfg.state_file)
    account = _build_account(cfg, state, client)

    feed: Optional[RealtimeFeed] = None
    if cfg.use_websocket:
        feed = RealtimeFeed(cfg, client.base_url)
        feed.start(cfg.coins)

    notifier = make_notifier(cfg)
    mtf = MultiTimeframe(client, cfg) if (cfg.use_mtf_filter or cfg.use_macd_filter) else None
    ledger = Ledger(cfg.ledger_file)
    ctx = Context(
        cfg=cfg, client=client, account=account, state=state,
        feed=feed, notifier=notifier, mtf=mtf, ledger=ledger,
    )

    log.info(
        "Starting hyperbot mode=%s coins=%s interval=%s poll=%.0fs leverage=%s risk=%.2f%% max_positions=%d "
        "mtf=%s orderflow=%s notify=%s",
        cfg.mode, cfg.coins, cfg.interval, cfg.poll_seconds, cfg.leverage, cfg.risk_per_trade * 100,
        cfg.max_positions, cfg.use_mtf_filter, cfg.use_orderflow_filter, notifier.enabled,
    )
    notifier.send(f"🚀 hyperbot 启动 | 模式={cfg.mode} | 品种={cfg.coins} | 周期={cfg.interval}")

    try:
        last_day = state.day
        while True:
            try:
                for coin in cfg.coins:
                    tick(ctx, coin)
                # UTC day rollover -> push a daily realized-PnL summary
                if state.day != last_day:
                    summ = daily_summary(ledger, last_day)
                    notifier.send(
                        f"📊 日结 {last_day} | 已实现盈亏 {summ['pnl']:+.2f} | "
                        f"交易 {summ['n']} 笔 | 胜率 {summ['win_rate']:.0f}%"
                    )
                    last_day = state.day
            except KeyboardInterrupt:
                log.info("Interrupted by user. Exiting.")
                state.save()
                break
            except Exception:
                log.exception("tick round failed; will retry on next poll")
            time.sleep(cfg.poll_seconds)
    finally:
        if feed is not None:
            feed.stop()


def emergency_flat(cfg: Config) -> None:
    """Close every open position and exit. Requires live mode credentials."""
    setup_logging(cfg.log_level)
    if cfg.mode == "paper":
        log.info("paper mode: nothing to flatten on the exchange.")
        return
    client = HyperClient(cfg)
    state = State.load(cfg.state_file)
    account = LiveAccount(state, client)
    account.sync_positions()
    closed = 0
    for coin in cfg.coins:
        if account.position(coin).is_open():
            log.info("EMERGENCY FLAT: closing %s.", coin)
            account.close(coin)
            closed += 1
    state.save()
    log.info("Done. Closed %d position(s).", closed)

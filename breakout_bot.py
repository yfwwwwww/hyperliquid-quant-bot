"""Donchian breakout bot: close above N-bar high -> long, below N-bar low -> short.
Exit: close crosses M-bar low/high, or ATR stop. Live-capable via config.mode."""
from __future__ import annotations

import logging
import time
from typing import Set

from .account import Position, State
from .bot import _build_account, setup_logging
from .client import HyperClient
from .config import Config
from .indicators import atr, ema
from .ledger import Ledger
from .notify import make_notifier
from .risk import compute_position_size

log = logging.getLogger("hyperbot.breakout")
_floor_notified: Set[str] = set()
_floor_strikes: Dict[str, int] = {}


def _tick(cfg: Config, client: HyperClient, account, state: State, ledger: Ledger, notifier, coin: str) -> None:
    candles = client.get_candles(coin, cfg.interval, cfg.candle_count)
    n = len(candles)
    N, M = cfg.breakout_n, cfg.breakout_m
    if n < max(N, M) + 2:
        return
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]
    a = atr(candles, cfg.atr_period)[-1]
    mark = closes[-1]
    if cfg.mode != "paper":
        try:
            mark = client.get_mid(coin)
        except Exception:
            pass

    state.ensure_day()
    eq = client.get_equity() if cfg.mode != "paper" else account.realized_equity()

    # hard equity floor: require N consecutive confirmations before halting
    at_floor = cfg.min_equity_stop > 0 and eq <= cfg.min_equity_stop
    if at_floor:
        _floor_strikes[coin] = _floor_strikes.get(coin, 0) + 1
        if _floor_strikes[coin] >= cfg.floor_confirm_ticks:
            if coin not in _floor_notified:
                _floor_notified.add(coin)
                log.critical("FLOOR coin=%s equity=%.2f <= %.2f", coin, eq, cfg.min_equity_stop)
                notifier.send(f"⛔ 硬地板触发：权益 {eq:.2f} ≤ {cfg.min_equity_stop:.2f}，停止交易")
            pos = account.position(coin)
            if pos.is_open():
                account.close(coin, mark)
                notifier.send(f"⛔ 硬地板：已强制平仓 {coin}")
            state.save()
            return
        log.warning(
            "FLOOR suspected coin=%s equity=%.2f <= %.2f (%d/%d); no new entries",
            coin, eq, cfg.min_equity_stop, _floor_strikes[coin], cfg.floor_confirm_ticks,
        )
    else:
        _floor_strikes[coin] = 0

    pos = account.position(coin)
    if pos.is_open():
        stop = pos.stop or 0.0
        reason = None
        if (pos.side == "long" and mark <= stop) or (pos.side == "short" and mark >= stop):
            reason = "stop"
        elif pos.side == "long" and closes[-1] < min(lows[-M - 1 : -1]):
            reason = "donchian"
        elif pos.side == "short" and closes[-1] > max(highs[-M - 1 : -1]):
            reason = "donchian"
        if reason:
            pnl = account.close(coin, mark)
            ledger.log_trade(coin, pos.side, "close", mark, pos.size, pnl=pnl, reason=reason)
            notifier.send(f"🔴 突破平仓 {coin} {pos.side} {reason} | {pnl:+.2f}")
            if reason == "stop":
                state.set_cooldown(coin, cfg.cooldown_seconds)
        state.save()
        return

    if at_floor:
        state.save()
        return
    if state.daily_halted(eq, cfg):
        state.save()
        return
    if state.in_cooldown(coin):
        state.save()
        return
    if account.open_count() >= cfg.max_positions:
        state.save()
        return

    up = max(highs[-N - 1 : -1])
    dn = min(lows[-N - 1 : -1])
    long_ok = closes[-1] > up
    short_ok = closes[-1] < dn
    if cfg.breakout_trend_filter and len(closes) >= cfg.breakout_trend_ema:
        trend = ema(closes, cfg.breakout_trend_ema)[-1]
        long_ok = long_ok and closes[-1] > trend
        short_ok = short_ok and closes[-1] < trend
    side = "long" if long_ok else ("short" if short_ok else None)
    if side is None:
        state.save()
        return

    size = compute_position_size(eq, mark, a, client.sz_decimals(coin), cfg)
    if size is None:
        state.save()
        return
    stop = mark - a * cfg.stop_atr_multiple if side == "long" else mark + a * cfg.stop_atr_multiple
    account.open(coin, side, size, mark, stop, None)
    ledger.log_trade(coin, side, "open", mark, size, reason="breakout")
    notifier.send(f"🟢 突破开仓 {coin} {side} @ {mark:.6g} size={size} stop={stop:.6g}")
    state.save()


def run_breakout(cfg: Config) -> None:
    setup_logging(cfg.log_level)
    client = HyperClient(cfg)
    state = State.load(cfg.state_file)
    account = _build_account(cfg, state, client)
    notifier = make_notifier(cfg)
    ledger = Ledger(cfg.ledger_file)

    log.info(
        "Starting breakout bot mode=%s coins=%s interval=%s N=%d M=%d stop=%sxATR risk=%.2f%% min_equity=%.2f",
        cfg.mode, cfg.coins, cfg.interval, cfg.breakout_n, cfg.breakout_m,
        cfg.stop_atr_multiple, cfg.risk_per_trade * 100, cfg.min_equity_stop,
    )
    notifier.send(f"🚀 breakout bot 启动 | {cfg.mode} | {cfg.coins} {cfg.interval}")

    try:
        while True:
            try:
                for coin in cfg.coins:
                    _tick(cfg, client, account, state, ledger, notifier, coin)
            except KeyboardInterrupt:
                log.info("Interrupted. Exiting.")
                state.save()
                break
            except Exception:
                log.exception("breakout tick failed; retry next poll")
            time.sleep(cfg.poll_seconds)
    finally:
        pass

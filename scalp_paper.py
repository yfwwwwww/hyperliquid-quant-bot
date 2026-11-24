"""Paper-only short-K runner: Bollinger+RSI entry, CVD (taker delta) gate,
wide ATR stop, TP1/TP2 partial exits, R-based sizing, ledger + notifications.

CVD is LIVE-ONLY (no historical taker data), so this runner needs the websocket
feed to gate entries. With `scalp_require_cvd: true` and no feed, it will not
enter. Run with `--scalp-paper` and leave it running to collect samples.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional

from .client import HyperClient
from .config import Config
from .feed import RealtimeFeed
from .indicators import atr, bollinger, rsi
from .ledger import Ledger
from .notify import make_notifier
from .risk import round_size_down

log = logging.getLogger("hyperbot.scalp_paper")


@dataclass
class SPos:
    side: str = ""
    size: float = 0.0
    remaining: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp1_taken: bool = False
    bars: int = 0
    pnl: float = 0.0
    entry_t: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "SPos":
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


class ScalpState:
    def __init__(self, path: str, start_equity: float):
        self.path = path
        self.equity = start_equity
        self.positions: Dict[str, SPos] = {}
        self.day = time.strftime("%Y-%m-%d", time.gmtime())
        self.day_start_equity = 0.0

    def ensure_day(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day = today
            self.day_start_equity = self.equity

    def halted(self, cfg: Config) -> bool:
        if self.day_start_equity <= 0:
            return False
        return self.equity <= self.day_start_equity * (1.0 - cfg.max_daily_loss_pct)

    def save(self) -> None:
        data = {
            "equity": self.equity,
            "positions": {c: p.to_dict() for c, p in self.positions.items()},
            "day": self.day,
            "day_start_equity": self.day_start_equity,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str, start_equity: float) -> "ScalpState":
        st = cls(path, start_equity)
        if os.path.exists(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
                st.equity = float(d.get("equity", start_equity))
                st.positions = {c: SPos.from_dict(p) for c, p in (d.get("positions") or {}).items()}
                st.day = d.get("day", st.day)
                st.day_start_equity = float(d.get("day_start_equity", 0.0) or 0.0)
            except Exception:
                log.warning("failed to load scalp state; starting fresh", exc_info=True)
        return st


def _close_part(cfg: Config, st: ScalpState, pos: SPos, coin: str, px: float, qty: float,
                reason: str, ledger: Ledger, notifier) -> None:
    if qty <= 0:
        return
    if pos.side == "long":
        pnl = (px - pos.entry) * qty - px * qty * cfg.fee_rate
    else:
        pnl = (pos.entry - px) * qty - px * qty * cfg.fee_rate
    st.equity += pnl
    pos.pnl += pnl
    pos.remaining -= qty
    ledger.log_trade(coin, pos.side, "close", px, qty, pnl=pnl, reason=reason)
    if pos.remaining <= 1e-12:
        notifier.send(f"🔴 短K平仓 {coin} {pos.side} {reason} | 本笔 {pos.pnl:+.2f} | 权益 {st.equity:.2f}")
        st.positions.pop(coin, None)


def _manage(cfg: Config, st: ScalpState, pos: SPos, coin: str, mark: float, ledger: Ledger, notifier) -> None:
    pos.bars += 1
    if pos.side == "long":
        if mark <= pos.stop:
            _close_part(cfg, st, pos, coin, pos.stop, pos.remaining, "stop", ledger, notifier)
        elif not pos.tp1_taken and mark >= pos.tp1:
            _close_part(cfg, st, pos, coin, pos.tp1, pos.size * cfg.scalp_tp1_frac, "tp1", ledger, notifier)
            pos.tp1_taken = True
            pos.stop = pos.entry
        elif pos.tp1_taken and mark >= pos.tp2:
            _close_part(cfg, st, pos, coin, pos.tp2, pos.remaining, "tp2", ledger, notifier)
    else:
        if mark >= pos.stop:
            _close_part(cfg, st, pos, coin, pos.stop, pos.remaining, "stop", ledger, notifier)
        elif not pos.tp1_taken and mark <= pos.tp1:
            _close_part(cfg, st, pos, coin, pos.tp1, pos.size * cfg.scalp_tp1_frac, "tp1", ledger, notifier)
            pos.tp1_taken = True
            pos.stop = pos.entry
        elif pos.tp1_taken and mark <= pos.tp2:
            _close_part(cfg, st, pos, coin, pos.tp2, pos.remaining, "tp2", ledger, notifier)
    if coin in st.positions and pos.bars >= cfg.scalp_max_bars:
        _close_part(cfg, st, pos, coin, mark, pos.remaining, "time", ledger, notifier)


def _tick(cfg: Config, client: HyperClient, feed: Optional[RealtimeFeed], ledger: Ledger,
          notifier, st: ScalpState, coin: str) -> None:
    candle_count = max(cfg.candle_count, cfg.scalp_max_bars * 4 + 200)
    candles = client.get_candles(coin, cfg.scalp_interval, candle_count)
    closes = [c["c"] for c in candles]
    _, upper, lower = bollinger(closes, cfg.scalp_bb_period, cfg.scalp_bb_mult)
    rsi_s = rsi(closes, cfg.scalp_rsi_period)
    atr_s = atr(candles, cfg.atr_period)
    i = len(candles) - 1
    mark = closes[i]
    if feed is not None:
        p = feed.latest_price(coin)
        if p:
            mark = p

    pos = st.positions.get(coin)
    if pos is not None:
        _manage(cfg, st, pos, coin, mark, ledger, notifier)
        return

    if st.halted(cfg):
        return

    long_sig = closes[i - 1] < lower[i - 1] and closes[i] > lower[i] and rsi_s[i] < cfg.scalp_rsi_buy
    short_sig = closes[i - 1] > upper[i - 1] and closes[i] < upper[i] and rsi_s[i] > (100.0 - cfg.scalp_rsi_buy)
    if not (long_sig or short_sig):
        return
    side = "long" if long_sig else "short"

    if cfg.scalp_require_cvd:
        if feed is None:
            return
        delta = feed.taker_delta(coin, cfg.scalp_cvd_window)
        if delta is None:
            return
        if side == "long" and delta <= 0:
            return
        if side == "short" and delta >= 0:
            return

    stop_dist = cfg.scalp_stop_atr * atr_s[i]
    if stop_dist <= 0 or mark <= 0 or st.equity <= 0:
        return
    raw = (st.equity * cfg.risk_per_trade) / stop_dist
    raw = min(raw, (st.equity * cfg.leverage * cfg.max_exposure) / mark)
    size = round_size_down(raw, client.sz_decimals(coin))
    if size <= 0 or size * mark < cfg.min_notional:
        return

    sign = 1 if side == "long" else -1
    entry = mark * (1 + cfg.slippage) if side == "long" else mark * (1 - cfg.slippage)
    st.equity -= entry * size * cfg.fee_rate
    pos = SPos(
        side=side, size=size, remaining=size, entry=entry,
        stop=entry - sign * stop_dist,
        tp1=entry + sign * cfg.scalp_r_mult1 * stop_dist,
        tp2=entry + sign * cfg.scalp_r_mult2 * stop_dist,
        entry_t=time.time(),
    )
    st.positions[coin] = pos
    ledger.log_trade(coin, side, "open", entry, size, reason="scalp_entry")
    notifier.send(
        f"🟢 短K开仓 {coin} {side} @ {entry:.6g} | size={size} | stop={pos.stop:.6g} "
        f"| TP1={pos.tp1:.6g} TP2={pos.tp2:.6g} | R风险={st.equity * cfg.risk_per_trade:.2f}"
    )


def run_scalp_paper(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    feed = RealtimeFeed(cfg, client.base_url) if cfg.use_websocket else None
    if feed is not None:
        feed.start(cfg.coins)
    notifier = make_notifier(cfg)
    ledger = Ledger(cfg.ledger_file)
    state_path = cfg.state_file.replace(".json", "_scalp.json")
    st = ScalpState.load(state_path, cfg.paper_start_equity)

    log.info(
        "Scalp paper started: coins=%s interval=%s stop=%sxATR TP=%.1fR/%.1fR cvd=%s feed=%s",
        cfg.coins, cfg.scalp_interval, cfg.scalp_stop_atr, cfg.scalp_r_mult1, cfg.scalp_r_mult2,
        cfg.scalp_require_cvd, feed is not None,
    )
    if cfg.scalp_require_cvd and feed is None:
        log.warning("scalp_require_cvd=True but use_websocket=False -> no entries will be taken.")
    try:
        while True:
            st.ensure_day()
            if st.day_start_equity <= 0:
                st.day_start_equity = st.equity
            for coin in cfg.coins:
                try:
                    _tick(cfg, client, feed, ledger, notifier, st, coin)
                except Exception:
                    log.exception("scalp tick failed for %s", coin)
            st.save()
            time.sleep(cfg.scalp_poll_seconds)
    except KeyboardInterrupt:
        log.info("Scalp paper stopped by user.")
    finally:
        if feed is not None:
            feed.stop()

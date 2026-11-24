"""10-second scout: watch for fresh entries and PUSH alerts (does not trade)."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .client import HyperClient
from .config import Config
from .feed import RealtimeFeed
from .mtf import MultiTimeframe
from .notify import make_notifier
from .orderflow import Orderflow, allows, read_orderflow
from .strategy import compute_indicators, long_ok, short_ok

log = logging.getLogger("hyperbot.scout")


@dataclass
class Candidate:
    coin: str
    side: str
    price: float
    agree: float
    imbalance: Optional[float]
    taker: Optional[float]
    ts: float


class Scout:
    def __init__(self, cfg: Config, client: HyperClient, feed: Optional[RealtimeFeed], notifier):
        self.cfg = cfg
        self.client = client
        self.feed = feed
        self.notifier = notifier
        self.mtf = MultiTimeframe(client, cfg)
        # coin -> (side, alert_expiry_ts)
        self._alerted: Dict[str, Tuple[str, float]] = {}

    def _is_fresh(self, coin: str, side: str) -> bool:
        prev = self._alerted.get(coin)
        if prev is None:
            return True
        prev_side, expiry = prev
        return prev_side != side or time.time() >= expiry

    def scan_once(self) -> List[Candidate]:
        out: List[Candidate] = []
        for coin in self.cfg.coins:
            try:
                funding = self.client.get_funding(coin) if self.cfg.funding_filter else None
                candles = self.client.get_candles(coin, self.cfg.interval, self.cfg.candle_count)
                snap = compute_indicators(candles, self.cfg, funding=funding)
                if snap.cross_up and long_ok(snap, self.cfg):
                    side = "long"
                elif snap.cross_down and short_ok(snap, self.cfg):
                    side = "short"
                else:
                    continue

                mtf = self.mtf.evaluate(coin, side)
                if self.cfg.use_mtf_filter and mtf.agree < self.cfg.mtf_min_agree:
                    continue
                if self.cfg.use_macd_filter and not mtf.macd_agrees(side):
                    continue

                of: Optional[Orderflow] = None
                if self.cfg.use_orderflow_filter:
                    of = read_orderflow(self.client, self.feed, coin, self.cfg)
                    if not allows(of, side, self.cfg):
                        continue

                if not self._is_fresh(coin, side):
                    continue
                self._alerted[coin] = (side, time.time() + self.cfg.scout_ttl_seconds)
                out.append(
                    Candidate(
                        coin=coin,
                        side=side,
                        price=snap.close,
                        agree=mtf.agree,
                        imbalance=of.imbalance if of else None,
                        taker=of.taker_buy_frac if of else None,
                        ts=time.time(),
                    )
                )
            except Exception:
                log.debug("scout scan failed for %s", coin, exc_info=True)
        return out

    def notify(self, c: Candidate) -> None:
        direction = "做多" if c.side == "long" else "做空"
        msg = (
            f"🛰️ 哨兵机会  {c.coin} {direction}\n"
            f"价格: {c.price:.6g}\n"
            f"多周期共振: {c.agree * 100:.0f}%"
        )
        if c.imbalance is not None:
            msg += f"\n盘口失衡(imb): {c.imbalance:+.2f}"
        if c.taker is not None:
            msg += f"\n主动买占比: {c.taker * 100:.0f}%"
        msg += "\n（仅提示，不自动下单；请自行判断风险）"
        self.notifier.send(msg)


def run_scout(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)
    client = HyperClient(cfg)
    feed = RealtimeFeed(cfg, client.base_url) if cfg.use_websocket else None
    if feed is not None:
        feed.start(cfg.coins)
    notifier = make_notifier(cfg)
    scout = Scout(cfg, client, feed, notifier)

    log.info(
        "Scout started: coins=%s interval=%.0fs ttl=%ds mtf=%s orderflow=%s",
        cfg.coins, cfg.scout_interval, cfg.scout_ttl_seconds, cfg.use_mtf_filter, cfg.use_orderflow_filter,
    )
    try:
        while True:
            for cand in scout.scan_once():
                scout.notify(cand)
                log.info(
                    "SCOUT %s %s agree=%.2f imb=%s taker=%s",
                    cand.coin, cand.side, cand.agree,
                    "%.2f" % cand.imbalance if cand.imbalance is not None else "-",
                    "%.2f" % cand.taker if cand.taker is not None else "-",
                )
            time.sleep(cfg.scout_interval)
    except KeyboardInterrupt:
        log.info("Scout stopped.")
    finally:
        if feed is not None:
            feed.stop()

"""Multi-timeframe resonance: align EMA/MACD direction across timeframes.

Live use: fetch candles per timeframe and compute direction per timeframe.
Backtest use: `align_directions` maps higher-timeframe directions onto base bars.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .client import HyperClient
from .config import Config
from .indicators import ema, macd


@dataclass
class TimeframeView:
    timeframe: str
    direction: int  # +1 bullish, -1 bearish, 0 flat
    fast: float
    slow: float
    macd_hist: float


def direction(closes: Sequence[float], fast: int, slow: int) -> int:
    if len(closes) < slow + 1:
        return 0
    ef = ema(closes, fast)[-1]
    es = ema(closes, slow)[-1]
    if ef > es:
        return 1
    if ef < es:
        return -1
    return 0


def direction_series(closes: Sequence[float], fast: int, slow: int) -> List[int]:
    ef = ema(closes, fast)
    es = ema(closes, slow)
    return [1 if a > b else (-1 if a < b else 0) for a, b in zip(ef, es)]


def align_directions(
    base_times: Sequence[int],
    htf_open_times: Sequence[int],
    htf_dirs: Sequence[int],
    htf_ms: int,
) -> List[int]:
    """For each base bar time, the direction of the last CLOSED higher-TF candle."""
    out: List[int] = []
    for t in base_times:
        # last htf candle whose close (open + htf_ms) <= t
        idx = bisect.bisect_right(htf_open_times, t - htf_ms) - 1
        out.append(htf_dirs[idx] if idx >= 0 else 0)
    return out


@dataclass
class MTFResult:
    views: Dict[str, TimeframeView]
    agree: float  # fraction of timeframes agreeing with the given side

    def macd_agrees(self, side: str) -> bool:
        want = 1 if side == "long" else -1
        if not self.views:
            return True
        agree = sum(1 for v in self.views.values() if (v.macd_hist >= 0) == (want > 0))
        return agree / len(self.views) >= 0.5


class MultiTimeframe:
    """Live multi-timeframe resonance calculator."""

    def __init__(self, client: HyperClient, cfg: Config):
        self.client = client
        self.cfg = cfg

    def views(self, coin: str) -> Dict[str, TimeframeView]:
        out: Dict[str, TimeframeView] = {}
        for tf in self.cfg.mtf_timeframes:
            candles = self.client.get_candles(coin, tf, self.cfg.mtf_candle_count)
            closes = [c["c"] for c in candles]
            hist = macd(closes, 12, 26, 9)[-1] if len(closes) > 26 else 0.0
            out[tf] = TimeframeView(
                timeframe=tf,
                direction=direction(closes, self.cfg.mtf_fast, self.cfg.mtf_slow),
                fast=ema(closes, self.cfg.mtf_fast)[-1] if len(closes) > self.cfg.mtf_fast else 0.0,
                slow=ema(closes, self.cfg.mtf_slow)[-1] if len(closes) > self.cfg.mtf_slow else 0.0,
                macd_hist=hist,
            )
        return out

    def evaluate(self, coin: str, side: str) -> MTFResult:
        views = self.views(coin)
        want = 1 if side == "long" else -1
        agree = sum(1 for v in views.values() if v.direction == want) / len(views) if views else 0.0
        return MTFResult(views=views, agree=agree)

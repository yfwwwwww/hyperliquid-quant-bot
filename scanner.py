"""Step 1: 10-second multi-symbol scanner.

For each symbol it computes the features the HF engines need:
price/changes, EMA trend (5m), ATR%, Donchian breakout state,
CVD (taker delta from websocket trades), L2 orderbook imbalance,
volume ratio, and a simple composite score.

Outputs a table to stdout and writes scan.json for the web dashboard.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, List, Optional

from .basis import basis_pct, stock_basis_pct
from .client import HyperClient
from .config import Config
from .feed import RealtimeFeed
from .indicators import atr, ema
from .orderflow import orderbook_imbalance, orderbook_walls
from .oi import OITracker

log = logging.getLogger("hyperbot.scanner")

DONCHIAN_N = 20
_oi = OITracker()


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def scan_once(cfg: Config, clients: Dict[str, HyperClient], feed: Optional[RealtimeFeed]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for coin in cfg.scan_symbols:
        client = clients.get("xyz") if coin.startswith("xyz:") else clients.get("main")
        if client is None:
            rows.append({"coin": coin, "error": "no client for dex"})
            continue
        try:
            cs5 = client.get_candles(coin, "5m", 300)
            cs1h = client.get_candles(coin, "1h", 48)
        except Exception as exc:
            rows.append({"coin": coin, "error": str(exc)[:80]})
            continue
        closes = [c["c"] for c in cs5]
        highs = [c["h"] for c in cs5]
        lows = [c["l"] for c in cs5]
        vols = [c["v"] for c in cs5]
        if len(closes) < 60:
            rows.append({"coin": coin, "error": "not enough candles"})
            continue
        i = len(closes) - 1
        price = closes[i]
        ef = ema(closes, 20)[i]
        es = ema(closes, 50)[i]
        et = ema(closes, 200)[i] if len(closes) >= 200 else es
        a = atr(cs5, 14)[i]
        up = max(highs[i - DONCHIAN_N : i])
        dn = min(lows[i - DONCHIAN_N : i])
        vol_ratio = (sum(vols[-20:]) / 20.0) / (sum(vols[-100:]) / 100.0) if len(vols) >= 100 else 0.0

        trend = 0
        if price > ef > es:
            trend = 1
        elif price < ef < es:
            trend = -1
        breakout = 0
        if price > up:
            breakout = 1
        elif price < dn:
            breakout = -1
        cvd = feed.taker_delta(coin, cfg.scalp_cvd_window) if feed is not None else None
        imb = orderbook_imbalance(client, coin, cfg.orderbook_levels)
        ctx = client.asset_ctx(coin)
        funding = float(ctx.get("funding", 0) or 0)
        oi = float(ctx.get("openInterest", 0) or 0)
        oi_notional = oi * price
        spread_bps = client.get_spread(coin)
        walls = orderbook_walls(client, coin, cfg.orderbook_levels)
        if oi > 0:
            _oi.update(coin, oi)
        score = trend + breakout
        if cvd is not None:
            score += 1 if cvd > 0.1 else (-1 if cvd < -0.1 else 0)
        if imb is not None:
            score += 1 if imb > cfg.orderflow_min_imb else (-1 if imb < -cfg.orderflow_min_imb else 0)

        row = {
            "coin": coin,
            "price": price,
            "chg_1h": _pct(cs1h[-1]["c"], cs1h[-2]["c"]) if len(cs1h) >= 2 else 0.0,
            "chg_24h": _pct(cs1h[-1]["c"], cs1h[-25]["c"]) if len(cs1h) >= 25 else 0.0,
            "ema20": ef, "ema50": es, "ema200": et,
            "trend": trend, "breakout": breakout,
            "atr_pct": a / price * 100.0 if price else 0.0,
            "vol_ratio": vol_ratio,
            "cvd": cvd, "imbalance": imb,
            "basis_pct": stock_basis_pct(coin, price) if coin.startswith("xyz:") else basis_pct(coin, price),
            "funding_apr": funding * 24 * 365 * 100.0,
            "oi_notional": oi_notional,
            "spread_bps": spread_bps,
            "wall_imb": walls.get("wall_imb") if walls else None,
            "oi_change_1h": _oi.change(coin, 3600),
            "score": score,
        }
        rows.append(row)
    rows.sort(key=lambda r: abs(r.get("score", 0)), reverse=True)
    return rows


def run_scanner(cfg: Config) -> None:
    from .bot import setup_logging
    import copy

    setup_logging(cfg.log_level)
    main_cfg = copy.copy(cfg)
    main_cfg.perp_dexs = []
    clients: Dict[str, HyperClient] = {"main": HyperClient(main_cfg)}
    if any(c.startswith("xyz:") for c in cfg.scan_symbols):
        xyz_cfg = copy.copy(cfg)
        xyz_cfg.perp_dexs = ["xyz"]
        clients["xyz"] = HyperClient(xyz_cfg)
    client = clients["main"]
    feed = RealtimeFeed(cfg, client.base_url) if cfg.use_websocket else None
    if feed is not None:
        feed.start(cfg.scan_symbols)
    log.info("scanner started: symbols=%s every %ss", cfg.scan_symbols, cfg.scan_interval_seconds)
    try:
        while True:
            rows = scan_once(cfg, clients, feed)
            print(f"\n=== scan {time.strftime('%H:%M:%S')} ===")
            print(f"{'coin':10} {'price':>10} {'24h%':>7} {'fund%':>7} {'spread':>7} {'cvd':>6} {'imb':>6} {'basis':>6} {'score':>5}")
            for r in rows:
                if "error" in r:
                    print(f"{r['coin']:10} error: {r['error']}")
                    continue
                print(
                    f"{r['coin']:10} {r['price']:10.4g} {r['chg_24h']:7.2f} "
                    f"{r.get('funding_apr', 0):7.2f} "
                    f"{(r.get('spread_bps') if r.get('spread_bps') is not None else 0):7.2f} "
                    f"{(r['cvd'] if r['cvd'] is not None else 0):6.2f} "
                    f"{(r['imbalance'] if r['imbalance'] is not None else 0):6.2f} "
                    f"{(r['basis_pct'] if r.get('basis_pct') is not None else 0):6.2f} "
                    f"{r['score']:5d}"
                )
            sys.stdout.flush()
            try:
                with open("scan.json", "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "rows": rows}, f, ensure_ascii=False, indent=2)
            except Exception:
                log.warning("failed to write scan.json", exc_info=True)
            _oi.save()
            time.sleep(cfg.scan_interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        if feed is not None:
            try:
                feed.stop()
            except Exception:
                pass

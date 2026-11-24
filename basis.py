"""Step 3 (complete version): cross-venue basis.

Spot vs perp premium, used as an extra edge/filter:
  basis% = (perp / spot - 1) * 100

Sources (with fallback):
  - Binance spot ticker
  - OKX spot ticker
  - Coinbase spot ticker

Cached for a few seconds to avoid hammering the APIs.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

_CACHE: Dict[str, Tuple[float, float]] = {}
_TTL = 10.0

_SYMBOLS = {
    "BTC": ("BTCUSDT", "BTC-USDT", "BTC-USD"),
    "ETH": ("ETHUSDT", "ETH-USDT", "ETH-USD"),
    "SOL": ("SOLUSDT", "SOL-USDT", "SOL-USD"),
    "HYPE": ("HYPEUSDT", "HYPE-USDT", None),
    "DOGE": ("DOGEUSDT", "DOGE-USDT", "DOGE-USD"),
    "AVAX": ("AVAXUSDT", "AVAX-USDT", "AVAX-USD"),
    "LINK": ("LINKUSDT", "LINK-USDT", "LINK-USD"),
    "ADA": ("ADAUSDT", "ADA-USDT", "ADA-USD"),
}

# xyz stock/commodity perps -> Yahoo Finance symbol
_YAHOO = {
    "xyz:TSLA": "TSLA",
    "xyz:NVDA": "NVDA",
    "xyz:SP500": "^GSPC",
    "xyz:GOLD": "GC=F",
}
_STOCK_CACHE: Dict[str, Tuple[float, float]] = {}


def _get(url: str, timeout: float = 5.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hyperbot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _binance(sym: str) -> Optional[float]:
    d = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}")
    try:
        return float(d["price"]) if d else None
    except Exception:
        return None


def _okx(sym: str) -> Optional[float]:
    d = _get(f"https://www.okx.com/api/v5/market/ticker?instId={sym}")
    try:
        return float(d["data"][0]["last"]) if d and d.get("data") else None
    except Exception:
        return None


def _coinbase(sym: str) -> Optional[float]:
    d = _get(f"https://api.exchange.coinbase.com/products/{sym}/ticker")
    try:
        return float(d["price"]) if d else None
    except Exception:
        return None


def fetch_spot(coin: str) -> Optional[float]:
    if coin not in _SYMBOLS:
        return None
    now = time.time()
    hit = _CACHE.get(coin)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    b, o, c = _SYMBOLS[coin]
    px = _binance(b) or _okx(o) or (_coinbase(c) if c else None)
    if px is not None:
        _CACHE[coin] = (now, px)
    return px


def basis_pct(coin: str, perp_price: float) -> Optional[float]:
    spot = fetch_spot(coin)
    if spot is None or spot <= 0 or perp_price <= 0:
        return None
    return (perp_price / spot - 1.0) * 100.0


def fetch_stock_spot(coin: str) -> Optional[float]:
    sym = _YAHOO.get(coin)
    if not sym:
        return None
    now = time.time()
    hit = _STOCK_CACHE.get(coin)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym, safe="") + "?interval=1m&range=1d")
    d = _get(url)
    try:
        px = float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        return None
    _STOCK_CACHE[coin] = (now, px)
    return px


def stock_basis_pct(coin: str, perp_price: float) -> Optional[float]:
    spot = fetch_stock_spot(coin)
    if spot is None or spot <= 0 or perp_price <= 0:
        return None
    return (perp_price / spot - 1.0) * 100.0

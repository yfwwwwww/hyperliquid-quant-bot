"""One-shot live smoke order: open a tiny position and immediately close it.

Purpose: verify that signing + order submission + close actually work, without
waiting for a strategy signal. Uses REAL money (min notional ~$11) when
mode=mainnet. Run on testnet first if possible.

Usage:
  set -a; source .env; set +a
  ./start.sh config.live.first.json --smoke-order
"""
from __future__ import annotations

import math
import sys
import time

from .client import HyperClient
from .config import Config


def run_smoke_order(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)

    if cfg.mode == "paper":
        print("smoke-order requires mode=testnet or mainnet with keys.")
        return

    client = HyperClient(cfg)
    coin = cfg.coins[0]
    px = client.get_mid(coin)
    d = client.sz_decimals(coin)
    step = 10 ** (-d)
    target_notional = max(cfg.min_notional, 11.0)
    size = math.ceil((target_notional / px) / step) * step
    notional = size * px

    print(f"About to OPEN {size} {coin} (~${notional:.2f}) and then CLOSE it.")
    if cfg.mode == "mainnet":
        print("*** This is a REAL order on MAINNET. ***")
    print("Type 'yes' to continue:", end=" ")
    if sys.stdin.readline().strip().lower() != "yes":
        print("aborted.")
        return

    resp = client.market_open(coin, True, size, cfg.slippage)
    print("OPEN response:", resp)
    if not client.ok(resp):
        print("OPEN FAILED; nothing to close.")
        return

    time.sleep(3)
    resp2 = client.market_close(coin, None, cfg.slippage)
    print("CLOSE response:", resp2)
    if not client.ok(resp2):
        print("CLOSE FAILED — CHECK THE UI AND CLOSE MANUALLY.")
        return
    print("Done. Check the Hyperliquid UI: position should be flat.")

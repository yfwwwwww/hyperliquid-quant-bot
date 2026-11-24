"""Parameter search scored on OUT-OF-SAMPLE (test) returns to reduce overfitting.

Prints in-sample (train) vs out-of-sample (test) side by side so you can see
whether apparent "edges" survive outside the fitted window.
"""
from __future__ import annotations

import copy
import itertools
from typing import Dict, List, Optional

from .backtest import _backtest
from .client import HyperClient
from .config import Config

# Search space (keep it modest; a big grid over-fits even the test window).
_GRID = {
    "ema_slow": [50, 100, 200],
    "stop_atr_multiple": [2.0, 3.0, 4.0],
    "adx_min": [0.0, 20.0, 25.0],
    "momentum_period": [0, 10, 20],
    "atr_max_pct": [0.0, 0.04, 0.06],
}


def run_optimize(cfg: Config, coins: Optional[List[str]] = None, top: int = 15) -> None:
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    coins = coins or cfg.coins
    alloc = cfg.paper_start_equity / len(coins)

    print(f"Fetching {cfg.interval} candles for {coins} ...")
    data = []
    for coin in coins:
        candles = client.get_candles(coin, cfg.interval, 5000)
        sz = client.sz_decimals(coin)
        split = int(len(candles) * 0.7)
        data.append((coin, candles, sz, split))

    keys = list(_GRID.keys())
    results: List[Dict] = []
    total = 1
    for v in _GRID.values():
        total *= len(v)
    done = 0
    for values in itertools.product(*_GRID.values()):
        combo = dict(zip(keys, values))
        c2 = copy.copy(cfg)
        for k, v in combo.items():
            setattr(c2, k, v)
        train_rets, test_rets = [], []
        for coin, candles, sz, split in data:
            train_rets.append(_backtest(candles[:split], sz, c2, alloc)["ret_pct"])
            test_rets.append(_backtest(candles[split:], sz, c2, alloc)["ret_pct"])
        score = sum(test_rets) / len(test_rets)
        results.append(
            {
                "combo": combo,
                "train": sum(train_rets) / len(train_rets),
                "test": score,
                "test_by_coin": test_rets,
            }
        )
        done += 1

    results.sort(key=lambda r: r["test"], reverse=True)

    print(f"\n========== OPTIMIZATION (scored by out-of-sample test return, {len(coins)} coins) ==========")
    print(f"interval={cfg.interval}  ema_fast={cfg.ema_fast}  ema_trend={cfg.ema_trend}  "
          f"trend_filter={cfg.use_trend_filter}")
    header = f"{'slow':>4} {'stop':>4} {'adx':>5} {'mom':>4} {'atrM':>5} | {'train%':>8} {'TEST%':>8}  {'test by coin'}"
    print(header)
    print("-" * 90)
    for r in results[:top]:
        c = r["combo"]
        by = " ".join(f"{v:.1f}" for v in r["test_by_coin"])
        print(
            f"{c['ema_slow']:>4} {c['stop_atr_multiple']:>4.1f} {c['adx_min']:>5.0f} "
            f"{c['momentum_period']:>4} {c['atr_max_pct']:>5.2f} | {r['train']:>8.2f} {r['test']:>8.2f}  {by}"
        )
    print("-" * 90)
    print("train = in-sample (first 70%), test = out-of-sample (last 30%).\n"
          "Choose configs whose TEST column is robustly positive across coins; beware big train->test gaps.")

"""HF paper performance report from the HF ledger.

Usage:
  ./start.sh config.hf_paper.json --hf-report
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .config import Config


def _read(path: str) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return recs
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    return recs


def run_hf_report(cfg: Config) -> None:
    recs = _read(cfg.ledger_file)
    closes = [r for r in recs if r.get("kind") == "trade" and r.get("action") == "close"]
    opens = [r for r in recs if r.get("kind") == "trade" and r.get("action") == "open"]
    equity = [r for r in recs if r.get("kind") == "equity"]

    pnls = [float(r.get("pnl") or 0.0) for r in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    win_rate = len(wins) / len(pnls) * 100.0 if pnls else 0.0
    avg = total / len(pnls) if pnls else 0.0

    eq_vals = [float(r.get("equity", 0)) for r in equity]
    dd = 0.0
    peak = 0.0
    for v in eq_vals:
        peak = max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak * 100.0)

    days = 0.0
    if opens:
        span = max(r.get("ts", 0) for r in opens) - min(r.get("ts", 0) for r in opens)
        days = max(span / 86400.0, 1.0 / 24.0)
    per_day = len(opens) / days if days > 0 else 0.0

    print("=" * 60)
    print("HF PAPER REPORT")
    print("=" * 60)
    print(f"ledger          : {cfg.ledger_file}")
    print(f"equity records  : {len(equity)}")
    print(f"opens / closes  : {len(opens)} / {len(closes)}")
    print(f"trades per day  : {per_day:.2f}")
    print(f"total pnl       : {total:+.2f}")
    if eq_vals:
        print(f"equity now      : {eq_vals[-1]:.2f} (start {eq_vals[0]:.2f})")
    print(f"win rate        : {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"avg pnl/trade   : {avg:+.3f}")
    print(f"profit factor   : {pf:.2f}")
    print(f"max drawdown    : {dd:.1f}%")
    print("=" * 60)
    if not closes:
        print("No closed trades yet. Let the paper bot run.")

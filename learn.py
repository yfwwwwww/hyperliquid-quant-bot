"""Learning report: which market contexts actually worked?

Reads journal_hf.jsonl (open/close records with context) and groups closed
trades by context buckets (basis, funding, cvd, imb, spread) to show win rate
and average PnL per bucket.

Usage:
  ./start.sh config.hf_live.json --learn
"""
from __future__ import annotations

from typing import Any, Dict, List

from .config import Config
from .journal import Journal


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    wins = [p for p in pnls if p > 0]
    return {
        "n": len(pnls),
        "win": (len(wins) / len(pnls) * 100.0) if pnls else 0.0,
        "avg": (sum(pnls) / len(pnls)) if pnls else 0.0,
        "sum": sum(pnls),
    }


def run_learn(cfg: Config) -> None:
    recs = Journal(cfg.journal_file).read(100000)
    closes = [r for r in recs if r.get("kind") == "close" and r.get("pnl") is not None]

    print("=" * 64)
    print("HF LEARNING REPORT")
    print("=" * 64)
    print(f"closed trades: {len(closes)}")
    if not closes:
        print("No closed trades yet. Let the bot run and collect data.")
        return

    overall = _stats(closes)
    print(f"overall: n={overall['n']} win={overall['win']:.1f}% avg={overall['avg']:+.3f} sum={overall['sum']:+.2f}")

    features = [
        ("basis", "基差%"),
        ("funding_apr", "资金费率年化%"),
        ("cvd", "CVD"),
        ("imb", "盘口失衡"),
        ("spread_bps", "价差bps"),
        ("wall_imb", "大单墙失衡"),
        ("oi_change_1h", "OI1h变化%"),
    ]
    for key, label in features:
        vals = []
        for r in closes:
            ctx = r.get("context") or {}
            v = ctx.get(key)
            if v is not None:
                vals.append((float(v), r))
        if len(vals) < 6:
            print(f"\n{label}: not enough samples ({len(vals)})")
            continue
        vals.sort(key=lambda x: x[0])
        mid = len(vals) // 2
        low = [r for _, r in vals[:mid]]
        high = [r for _, r in vals[mid:]]
        lo, hi = _stats(low), _stats(high)
        print(f"\n{label}:")
        print(f"  low  (<= {vals[mid][0]:+.4f}): n={lo['n']:2d} win={lo['win']:5.1f}% avg={lo['avg']:+.3f}")
        print(f"  high (>  {vals[mid][0]:+.4f}): n={hi['n']:2d} win={hi['win']:5.1f}% avg={hi['avg']:+.3f}")

    print("\n" + "=" * 64)
    print("Rule of thumb: only trust a bucket with n >= 30.")

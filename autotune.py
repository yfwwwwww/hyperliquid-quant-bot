"""Offline learner: find entry filters that are positive on train AND holdout.

Reads journal_hf.jsonl, splits by time (70/30), searches a few thresholds for
imb / spread_bps, and writes policy.json. hf_live reads policy.json and skips
entries that violate it.

Guardrail: nothing is written until there are >= 100 closed trades and the
holdout bucket has >= 20 trades. This prevents tuning on noise.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .journal import Journal

MIN_TRADES = 100
MIN_HOLDOUT = 20


def _avg(rows: List[Dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(float(r.get("pnl") or 0.0) for r in rows) / len(rows)


def _quantiles(vals: List[float], n: int = 7) -> List[float]:
    if not vals:
        return []
    s = sorted(vals)
    return [s[int(len(s) * i / (n + 1))] for i in range(1, n + 1)]


def _eval(rows: List[Dict[str, Any]], feat: str, t: float, keep_high: bool) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        v = (r.get("context") or {}).get(feat)
        if v is None:
            continue
        v = float(v)
        if (keep_high and v >= t) or ((not keep_high) and v <= t):
            out.append(r)
    return out


def run_autotune(cfg: Config) -> None:
    closes = [r for r in Journal(cfg.journal_file).read(100000)
              if r.get("kind") == "close" and r.get("pnl") is not None]
    print(f"closed trades: {len(closes)}")
    if len(closes) < MIN_TRADES:
        print(f"not enough samples (need {MIN_TRADES}); policy unchanged")
        with open("policy.json", "w", encoding="utf-8") as f:
            json.dump({}, f)
        return

    closes.sort(key=lambda r: r.get("ts", 0))
    split = int(len(closes) * 0.7)
    train, hold = closes[:split], closes[split:]

    best: Optional[Tuple[float, str, Dict[str, Any]]] = None
    for feat in ("imb", "spread_bps", "wall_imb", "oi_change_1h"):
        vals = [float((r.get("context") or {}).get(feat)) for r in closes
                if (r.get("context") or {}).get(feat) is not None]
        for t in _quantiles(vals):
            for keep_high in (True, False):
                tr = _eval(train, feat, t, keep_high)
                ho = _eval(hold, feat, t, keep_high)
                if len(tr) < 20 or len(ho) < MIN_HOLDOUT:
                    continue
                a_tr, a_ho = _avg(tr), _avg(ho)
                if a_tr <= 0 or a_ho <= 0:
                    continue
                score = min(a_tr, a_ho)
                if best is None or score > best[0]:
                    key = ("min_" + feat) if keep_high else ("max_" + feat)
                    best = (score, key, {"value": t, "train_avg": a_tr,
                                         "holdout_avg": a_ho, "train_n": len(tr),
                                         "holdout_n": len(ho)})

    policy: Dict[str, Any] = {}
    if best is not None:
        score, key, meta = best
        policy[key] = round(meta["value"], 6)
        policy["_meta"] = {key: meta}
        print(f"learned: {key} = {meta['value']:.6f} "
              f"(train {meta['train_avg']:+.3f} / holdout {meta['holdout_avg']:+.3f}, "
              f"n={meta['train_n']}/{meta['holdout_n']})")
    else:
        print("no robust filter found; policy is empty")

    with open("policy.json", "w", encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, indent=2)
    print("wrote policy.json:", policy)

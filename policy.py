"""Policy loader: entry filters learned offline by autotune.py."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

POLICY_PATH = "policy.json"


def load_policy(path: str = POLICY_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def policy_ok(policy: Dict[str, Any], ctx: Dict[str, Optional[float]]) -> bool:
    if not policy:
        return True
    for key, val in policy.items():
        if key.startswith("_"):
            continue
        if not isinstance(val, (int, float)):
            continue
        feat = key.split("_", 1)[1] if "_" in key else key
        v = ctx.get(feat)
        if v is None:
            continue
        if key.startswith("min_") and float(v) < float(val):
            return False
        if key.startswith("max_") and float(v) > float(val):
            return False
    return True

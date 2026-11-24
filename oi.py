"""Open-interest tracker: keeps the last 24h of OI snapshots in a JSON file."""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List

PATH = "oi_history.json"


class OITracker:
    def __init__(self, path: str = PATH):
        self.path = path
        self.data: Dict[str, List[List[float]]] = {}
        if os.path.exists(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.data = {}

    def update(self, coin: str, oi: float) -> None:
        now = time.time()
        arr = self.data.setdefault(coin, [])
        arr.append([now, float(oi)])
        cutoff = now - 86400
        self.data[coin] = [x for x in arr if x[0] >= cutoff]

    def change(self, coin: str, seconds: int = 3600) -> float:
        arr = self.data.get(coin) or []
        if len(arr) < 2:
            return 0.0
        target = time.time() - seconds
        old = None
        for ts, oi in arr:
            if ts <= target:
                old = oi
        if old is None or old <= 0:
            old = arr[0][1]
        return (arr[-1][1] / old - 1.0) * 100.0 if old > 0 else 0.0

    def save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

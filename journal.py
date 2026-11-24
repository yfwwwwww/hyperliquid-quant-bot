"""Trade journal: append-only JSONL with the market context at entry.

Each record:
  {ts, kind: open|close, coin, side, price, size, pnl, reason,
   context: {basis, cvd, imb, funding_apr, spread_bps}}
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


class Journal:
    def __init__(self, path: str = "journal.jsonl"):
        self.path = path

    def record(self, kind: str, coin: str, side: str, price: float, size: float,
               pnl: Optional[float] = None, reason: str = "",
               context: Optional[Dict[str, Any]] = None) -> None:
        rec = {
            "ts": time.time(), "kind": kind, "coin": coin, "side": side,
            "price": float(price), "size": float(size), "pnl": pnl,
            "reason": reason, "context": context or {},
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def read(self, limit: int = 50) -> List[Dict[str, Any]]:
        recs: List[Dict[str, Any]] = []
        if not os.path.exists(self.path):
            return recs
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            recs.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        return recs[-limit:]

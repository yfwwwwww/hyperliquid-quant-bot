"""Append-only JSONL ledger for trades and equity snapshots.

Each line is one JSON record:
  {"kind": "trade", "ts": ..., "coin": ..., "side": ..., "action": "open"/"close",
   "price": ..., "size": ..., "pnl": ..., "reason": ...}
  {"kind": "equity", "ts": ..., "equity": ..., "mode": ...}

The ledger is the source of truth for `--report`; it survives state.json resets.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self._last_equity_ts = 0.0

    # ------------------------------------------------------------------ write
    def _append(self, rec: Dict) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def log_trade(
        self,
        coin: str,
        side: str,
        action: str,
        price: float,
        size: float,
        pnl: Optional[float] = None,
        reason: str = "",
        ts: Optional[float] = None,
    ) -> None:
        self._append(
            {
                "kind": "trade",
                "ts": ts if ts is not None else time.time(),
                "coin": coin,
                "side": side,
                "action": action,
                "price": price,
                "size": size,
                "pnl": pnl,
                "reason": reason,
            }
        )

    def log_equity(self, equity: float, mode: str, ts: Optional[float] = None, min_interval: float = 1800.0) -> None:
        now = ts if ts is not None else time.time()
        if now - self._last_equity_ts < min_interval:
            return
        self._last_equity_ts = now
        self._append({"kind": "equity", "ts": now, "equity": equity, "mode": mode})

    # ------------------------------------------------------------------- read
    def read(self) -> List[Dict]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def trades(self) -> List[Dict]:
        return [r for r in self.read() if r.get("kind") == "trade"]

    def equity(self) -> List[Dict]:
        return [r for r in self.read() if r.get("kind") == "equity"]

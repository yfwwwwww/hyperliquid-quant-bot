"""Performance report from the ledger: return, drawdown, win rate, per-coin, daily."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .config import Config
from .ledger import Ledger


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def daily_summary(ledger: Ledger, day_str: str) -> Dict:
    """Realized PnL summary for one UTC day, from closed trades."""
    closes = [
        t
        for t in ledger.trades()
        if t.get("action") == "close" and t.get("pnl") is not None and _utc_day(t["ts"]) == day_str
    ]
    pnl = sum(float(t["pnl"]) for t in closes)
    wins = sum(1 for t in closes if float(t["pnl"]) > 0)
    n = len(closes)
    return {"day": day_str, "pnl": pnl, "n": n, "win_rate": (wins / n * 100) if n else 0.0}


def run_report(cfg: Config, ledger_path: Optional[str] = None) -> None:
    ledger = Ledger(ledger_path or cfg.ledger_file)
    trades = ledger.trades()
    equity = ledger.equity()
    closes = [t for t in trades if t.get("action") == "close" and t.get("pnl") is not None]

    if not trades and not equity:
        print(f"No ledger records yet at {ledger.path!r}.")
        print("Run the bot in paper mode for a while, then re-run --report.")
        return

    # Equity curve metrics.
    if equity:
        eq_start = float(equity[0]["equity"])
        eq_end = float(equity[-1]["equity"])
        peak = eq_start
        max_dd = 0.0
        for rec in equity:
            v = float(rec["equity"])
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
        total_ret = (eq_end - eq_start) / eq_start * 100 if eq_start else 0.0
    else:
        eq_start = eq_end = cfg.paper_start_equity
        max_dd = 0.0
        total_ret = 0.0

    realized = sum(float(t["pnl"]) for t in closes)
    wins = [t for t in closes if float(t["pnl"]) > 0]
    losses = [t for t in closes if float(t["pnl"]) <= 0]
    gross_win = sum(float(t["pnl"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl"]) for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    win_rate = len(wins) / len(closes) * 100 if closes else 0.0

    print("\n========== PERFORMANCE REPORT ==========")
    print(f"ledger: {ledger.path}")
    print(f"records: {len(trades)} trades, {len(equity)} equity snapshots")
    print(f"equity:   {eq_start:,.2f} -> {eq_end:,.2f}   ({(eq_end - eq_start) / eq_start * 100 if eq_start else 0:+.2f}%)")
    print(f"max drawdown:        {max_dd * 100:.2f}%")
    print(f"realized PnL:        {realized:+,.2f}")
    print(f"round trips:         {len(closes)}")
    print(f"win rate:            {win_rate:.1f}%")
    print(f"profit factor:       {'inf' if pf == float('inf') else f'{pf:.2f}'}")
    print(f"gross win / loss:    {gross_win:,.2f} / {gross_loss:,.2f}")

    # Per-coin breakdown.
    if closes:
        by_coin: Dict[str, List[float]] = {}
        for t in closes:
            by_coin.setdefault(t["coin"], []).append(float(t["pnl"]))
        print("\n-- by coin --")
        for coin, pnls in sorted(by_coin.items(), key=lambda kv: sum(kv[1]), reverse=True):
            w = sum(1 for p in pnls if p > 0)
            print(f"  {coin:8} trades={len(pnls):3}  pnl={sum(pnls):+9.2f}  win={w / len(pnls) * 100:5.1f}%")

    # Daily breakdown.
    if closes:
        by_day: Dict[str, List[float]] = {}
        for t in closes:
            by_day.setdefault(_utc_day(t["ts"]), []).append(float(t["pnl"]))
        print("\n-- by day (last 14) --")
        for day in sorted(by_day)[-14:]:
            pnls = by_day[day]
            w = sum(1 for p in pnls if p > 0)
            print(f"  {day}  pnl={sum(pnls):+9.2f}  trades={len(pnls):3}  win={w / len(pnls) * 100:5.1f}%")

    print("========================================\n")

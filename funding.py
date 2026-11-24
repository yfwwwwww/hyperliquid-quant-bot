"""Funding-rate research: does funding-carry / contrarian positioning have an edge?

Funding is the per-hour transfer between longs and shorts on a perpetual. This
module tests two simple, transparent hypotheses out-of-sample:

  contrarian: short when trailing funding is very positive, long when very negative
  momentum:   long when trailing funding is positive, short when negative

P&L per bar = pos * price_ret - pos * funding_sum_bar  (1x notional, fees on turnover)
"""
from __future__ import annotations

import copy
import time
from typing import Dict, List, Optional, Tuple

from .client import HyperClient
from .config import Config

CHUNK_MS = 20 * 86_400 * 1000  # API returns <=500 hourly rows per call (~20 days)


def fetch_funding(client: HyperClient, coin: str, start_ms: int, end_ms: int) -> List[Tuple[int, float]]:
    seen: Dict[int, float] = {}
    cursor = start_ms
    while cursor < end_ms:
        nxt = min(cursor + CHUNK_MS, end_ms)
        part = client.get_funding_history(coin, cursor, nxt)
        for r in part:
            seen[int(r["time"])] = float(r["fundingRate"])
        cursor = nxt
    return sorted(seen.items())


def funding_per_bar(candles: List[Dict[str, float]], funding: List[Tuple[int, float]], bar_ms: int) -> List[float]:
    """Sum hourly funding rates falling inside each candle's [open, open+bar_ms)."""
    out: List[float] = []
    j = 0
    n = len(funding)
    for c in candles:
        lo = int(c["t"])
        hi = lo + bar_ms
        while j < n and funding[j][0] < lo:
            j += 1
        s = 0.0
        k = j
        while k < n and funding[k][0] < hi:
            s += funding[k][1]
            k += 1
        out.append(s)
    return out


def _carry_backtest(
    candles: List[Dict[str, float]],
    f_bar: List[float],
    cfg: Config,
    start_equity: float,
    window_bars: int,
    apr_thr: float,
    contrarian: bool,
) -> Dict:
    bar_hours = cfg.interval_ms() / 3_600_000.0
    equity = start_equity
    pos = 0
    changes = 0
    carry_ret = 0.0
    curve: List[float] = []
    peak = equity
    mdd = 0.0

    for i in range(len(candles)):
        if i > 0:
            ret = candles[i]["c"] / candles[i - 1]["c"] - 1.0
            funding_ret = -pos * f_bar[i]
            equity *= 1.0 + pos * ret + funding_ret
            carry_ret += funding_ret

        if i >= window_bars:
            avg_hourly = sum(f_bar[i - window_bars + 1 : i + 1]) / (window_bars * bar_hours)
            apr = avg_hourly * 24 * 365
            target = 0
            if apr > apr_thr:
                target = -1 if contrarian else 1
            elif apr < -apr_thr:
                target = 1 if contrarian else -1
        else:
            target = 0

        if target != pos:
            equity -= abs(target - pos) * cfg.fee_rate * equity
            changes += 1
            pos = target

        curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak)

    return {
        "ret_pct": (equity - start_equity) / start_equity * 100 if start_equity else 0.0,
        "mdd_pct": mdd * 100,
        "changes": changes,
        "carry_pct": carry_ret * 100,
        "final_equity": equity,
    }


def run_funding_backtest(cfg: Config, coins: Optional[List[str]] = None, bars: int = 2190) -> None:
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    coins = coins or cfg.coins
    alloc = cfg.paper_start_equity / len(coins)
    window_bars = 6          # ~24h on 4h bars
    apr_thr = 0.08           # 8% APR; NOTE: results are threshold-sensitive (see README)
    bar_ms = cfg.interval_ms()

    print(f"\n===== FUNDING-CARRY BACKTEST (window={window_bars} bars, thr=±{apr_thr*100:.0f}% APR) =====")
    print(f"interval={cfg.interval} bars={bars} | P&L = pos*price_ret - pos*funding | 1x notional")
    print(f"{'coin':6} | {'contr full':>10} {'train':>8} {'TEST':>8} {'chg':>4} | "
          f"{'mom full':>9} {'train':>8} {'TEST':>8} {'chg':>4}")
    print("-" * 92)

    c_tot = m_tot = 0.0
    for coin in coins:
        candles = client.get_candles(coin, cfg.interval, bars)
        funding = fetch_funding(client, coin, int(candles[0]["t"]), int(candles[-1]["t"]))
        f_bar = funding_per_bar(candles, funding, bar_ms)
        split = int(len(candles) * 0.7)

        c_full = _carry_backtest(candles, f_bar, cfg, alloc, window_bars, apr_thr, True)
        c_train = _carry_backtest(candles[:split], f_bar[:split], cfg, alloc, window_bars, apr_thr, True)
        c_test = _carry_backtest(candles[split:], f_bar[split:], cfg, alloc, window_bars, apr_thr, True)
        m_full = _carry_backtest(candles, f_bar, cfg, alloc, window_bars, apr_thr, False)
        m_train = _carry_backtest(candles[:split], f_bar[:split], cfg, alloc, window_bars, apr_thr, False)
        m_test = _carry_backtest(candles[split:], f_bar[split:], cfg, alloc, window_bars, apr_thr, False)
        c_tot += c_full["final_equity"] - alloc
        m_tot += m_full["final_equity"] - alloc

        print(
            f"{coin:6} | {c_full['ret_pct']:10.2f} {c_train['ret_pct']:8.2f} {c_test['ret_pct']:8.2f} "
            f"{c_full['changes']:4} | {m_full['ret_pct']:9.2f} {m_train['ret_pct']:8.2f} "
            f"{m_test['ret_pct']:8.2f} {m_full['changes']:4}"
        )
        print(f"       carry-only contribution: contr {c_full['carry_pct']:+.2f}% | mom {m_full['carry_pct']:+.2f}%")

    print("-" * 92)
    print(f"COMBINED contrarian = {c_tot / cfg.paper_start_equity * 100:+.2f}%  |  "
          f"momentum = {m_tot / cfg.paper_start_equity * 100:+.2f}%")
    print("train=in-sample(70%), TEST=out-of-sample(30%). Trust TEST, not full-sample.\n")


def run_funding_yield(cfg: Config, coins: Optional[List[str]] = None, days: int = 30) -> None:
    """Quantify the delta-neutral funding carry: trailing funding APR and stability.

    A short-perp position of notional N receives N * sum(hourly funding). If you
    hold spot against it, price exposure ~0 and the funding is the return.
    """
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    coins = coins or cfg.coins
    end = int(time.time() * 1000)
    start = end - days * 86_400 * 1000

    print(f"\n===== FUNDING CARRY YIELD (trailing {days} days) =====")
    print("A short perp collects funding. Positive funding = shorts get paid.")
    print(f"{'coin':6} {'hours':>6} {'avg APR':>9} {'% pos hrs':>10} {'$10k PnL':>10}")
    print("-" * 46)
    for coin in coins:
        try:
            fh = fetch_funding(client, coin, start, end)
        except Exception:
            print(f"{coin:6}  (fetch failed)")
            continue
        if not fh:
            print(f"{coin:6}  (no funding data)")
            continue
        rates = [r for _, r in fh]
        total = sum(rates)
        apr = total / (days / 365.0) if days else 0.0
        pos_frac = sum(1 for r in rates if r > 0) / len(rates)
        pnl_10k = 10_000.0 * total
        print(f"{coin:6} {len(rates):6} {apr * 100:8.1f}% {pos_frac * 100:9.1f}% {pnl_10k:10.2f}")
    print("-" * 46)
    print("NOTE: this is funding-only carry (delta-neutral assumes you hold spot).")
    print("      Real risks: funding can flip negative, basis/oracle can diverge, and")
    print("      spot+perp capital + fees reduce the net yield. Not financial advice.\n")

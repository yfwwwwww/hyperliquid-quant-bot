"""Event-driven backtest with multi-coin support and out-of-sample (train/test) split."""
from __future__ import annotations

import copy
import csv
from typing import Dict, List, Optional, Tuple

from .client import HyperClient
from .config import Config, interval_to_ms
from .indicators import adx, atr, ema, roc
from .mtf import align_directions, direction_series
from .risk import compute_position_size
from .strategy import stop_and_target


def _cross_up(fast: List[float], slow: List[float], i: int) -> bool:
    return fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]


def _cross_down(fast: List[float], slow: List[float], i: int) -> bool:
    return fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]


def _backtest(
    candles: List[Dict[str, float]],
    sz_dec: int,
    cfg: Config,
    start_equity: float,
    mtf_long: Optional[List[bool]] = None,
    mtf_short: Optional[List[bool]] = None,
) -> Dict:
    """Run the strategy over `candles` and return metrics + trades + equity curve.

    Mirrors strategy.py's filters (funding filter is live-only and ignored here).
    `mtf_long`/`mtf_short` are optional per-bar gates from multi-timeframe resonance.
    """
    closes = [c["c"] for c in candles]
    ema_fast = ema(closes, cfg.ema_fast)
    ema_slow = ema(closes, cfg.ema_slow)
    ema_trend = ema(closes, cfg.ema_trend) if cfg.use_trend_filter else [0.0] * len(closes)
    atr_series = atr(candles, cfg.atr_period)
    adx_series = adx(candles, cfg.adx_period) if cfg.adx_min > 0 else [0.0] * len(closes)
    roc_series = roc(closes, cfg.momentum_period) if cfg.momentum_period > 0 else [0.0] * len(closes)

    warmup = cfg.warmup_bars()

    def long_ok_at(i: int) -> bool:
        if mtf_long is not None and not mtf_long[i]:
            return False
        if cfg.use_trend_filter and closes[i] <= ema_trend[i]:
            return False
        if cfg.adx_min > 0 and adx_series[i] < cfg.adx_min:
            return False
        if cfg.atr_max_pct > 0 and (atr_series[i] / closes[i]) > cfg.atr_max_pct:
            return False
        if cfg.momentum_period > 0 and roc_series[i] < cfg.momentum_min:
            return False
        return True

    def short_ok_at(i: int) -> bool:
        if mtf_short is not None and not mtf_short[i]:
            return False
        if cfg.use_trend_filter and closes[i] >= ema_trend[i]:
            return False
        if cfg.adx_min > 0 and adx_series[i] < cfg.adx_min:
            return False
        if cfg.atr_max_pct > 0 and (atr_series[i] / closes[i]) > cfg.atr_max_pct:
            return False
        if cfg.momentum_period > 0 and roc_series[i] > -cfg.momentum_min:
            return False
        return True

    equity = start_equity
    position: Optional[Dict[str, float]] = None
    pending: Optional[str] = None
    trades: List[Dict] = []
    equity_curve: List[float] = []
    peak = equity
    max_dd = 0.0

    def check_exit(pos: Dict[str, float], bar: Dict[str, float], i: int) -> Tuple[Optional[float], Optional[str]]:
        stop = pos["stop"]
        target = pos["target"]
        if pos["side"] == "long":
            if bar["l"] <= stop:
                return stop, "stop"
            if target and bar["h"] >= target:
                return target, "target"
            if _cross_down(ema_fast, ema_slow, i):
                return bar["c"], "cross"
        else:
            if bar["h"] >= stop:
                return stop, "stop"
            if target and bar["l"] <= target:
                return target, "target"
            if _cross_up(ema_fast, ema_slow, i):
                return bar["c"], "cross"
        return None, None

    def exit_net(pos: Dict[str, float], exit_px: float) -> float:
        size = pos["size"]
        entry = pos["entry"]
        if pos["side"] == "long":
            fill = exit_px * (1 - cfg.slippage)
            gross = (fill - entry) * size
        else:
            fill = exit_px * (1 + cfg.slippage)
            gross = (entry - fill) * size
        return gross - fill * size * cfg.fee_rate

    for i in range(len(candles)):
        bar = candles[i]

        if position is not None:
            exit_px, reason = check_exit(position, bar, i)
            if exit_px is not None:
                pnl = exit_net(position, exit_px)
                equity += pnl
                trades.append(
                    {
                        "side": position["side"],
                        "entry": position["entry"],
                        "exit": exit_px,
                        "size": position["size"],
                        "pnl": pnl,
                        "reason": reason,
                        "t": bar["t"],
                    }
                )
                position = None

        if position is None and pending is not None:
            size = compute_position_size(equity, bar["o"], atr_series[i], sz_dec, cfg)
            if size is not None:
                stop, target = stop_and_target(pending, bar["o"], atr_series[i], cfg)
                fill = bar["o"] * (1 + cfg.slippage) if pending == "long" else bar["o"] * (1 - cfg.slippage)
                fee = fill * size * cfg.fee_rate
                equity -= fee
                position = {"side": pending, "size": size, "entry": fill, "stop": stop, "target": target}
            pending = None

        if i >= warmup:
            if _cross_up(ema_fast, ema_slow, i) and long_ok_at(i):
                pending = "long"
            elif _cross_down(ema_fast, ema_slow, i) and short_ok_at(i):
                pending = "short"
            else:
                pending = None

        mark = equity
        if position is not None:
            if position["side"] == "long":
                mark += (bar["c"] - position["entry"]) * position["size"]
            else:
                mark += (position["entry"] - bar["c"]) * position["size"]
        equity_curve.append(mark)
        peak = max(peak, mark)
        if peak > 0:
            max_dd = max(max_dd, (peak - mark) / peak)

    if position is not None:
        pnl = exit_net(position, candles[-1]["c"])
        equity += pnl
        trades.append(
            {
                "side": position["side"],
                "entry": position["entry"],
                "exit": candles[-1]["c"],
                "size": position["size"],
                "pnl": pnl,
                "reason": "eod",
                "t": candles[-1]["t"],
            }
        )

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    return {
        "start_equity": start_equity,
        "final_equity": equity,
        "ret_pct": (equity - start_equity) / start_equity * 100 if start_equity else 0.0,
        "maxdd_pct": max_dd * 100,
        "n_trades": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "profit_factor": pf,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def run_backtest(cfg: Config, coins: Optional[List[str]] = None) -> None:
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"  # public market data only; no keys required
    client = HyperClient(data_cfg)
    coins = coins or cfg.coins
    alloc = cfg.paper_start_equity / len(coins)

    print(f"\n========== BACKTEST (equal capital per coin = {alloc:,.2f}) ==========")
    print(f"interval={cfg.interval}  ema={cfg.ema_fast}/{cfg.ema_slow}  stop={cfg.stop_atr_multiple}xATR  "
          f"adx_min={cfg.adx_min}  atr_max={cfg.atr_max_pct}  momentum={cfg.momentum_period}  trend={cfg.use_trend_filter}")
    print(f"{'coin':8} {'full%':>9} {'train%':>9} {'test%':>9} {'maxdd%':>8} {'trades':>7} {'win%':>7} {'pf':>7}")
    print("-" * 72)

    all_trades: List[Dict] = []
    total_pnl = 0.0
    for coin in coins:
        candles = client.get_candles(coin, cfg.interval, 5000)
        sz = client.sz_decimals(coin)
        split = int(len(candles) * 0.7)
        full = _backtest(candles, sz, cfg, alloc)
        train = _backtest(candles[:split], sz, cfg, alloc)
        test = _backtest(candles[split:], sz, cfg, alloc)
        total_pnl += full["final_equity"] - alloc
        for t in full["trades"]:
            t["coin"] = coin
        all_trades.extend(full["trades"])
        pf = full["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(
            f"{coin:8} {full['ret_pct']:9.2f} {train['ret_pct']:9.2f} {test['ret_pct']:9.2f} "
            f"{full['maxdd_pct']:8.2f} {full['n_trades']:7} {full['win_rate_pct']:7.1f} {pf_s:>7}"
        )

    print("-" * 72)
    print(f"COMBINED  total return = {total_pnl / cfg.paper_start_equity * 100:+.2f}%  "
          f"(sum PnL {total_pnl:+,.2f} on {cfg.paper_start_equity:,.2f})")
    print("NOTE: train = first 70% (in-sample), test = last 30% (out-of-sample).\n"
          "      A strategy that only wins in-sample is overfit. Funding filter is live-only.\n")

    with open("backtest_trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["coin", "side", "entry", "exit", "size", "pnl", "reason", "t"])
        w.writeheader()
        w.writerows(all_trades)
    print("Saved backtest_trades.csv")


def build_mtf_gates(candles, client, cfg, coin):
    """Per-bar long/short gates: all configured higher timeframes must agree."""
    if not cfg.mtf_timeframes:
        return None, None
    base_times = [c["t"] for c in candles]
    dirs_by_tf = []
    for tf in cfg.mtf_timeframes:
        htf = client.get_candles(coin, tf, 5000)
        if len(htf) < cfg.mtf_slow + 2:
            return None, None
        htf_times = [c["t"] for c in htf]
        htf_dirs = direction_series([c["c"] for c in htf], cfg.mtf_fast, cfg.mtf_slow)
        dirs_by_tf.append(align_directions(base_times, htf_times, htf_dirs, interval_to_ms(tf)))
    longs = [
        sum(1 for d in dirs_by_tf if d[i] == 1) / len(dirs_by_tf) >= cfg.mtf_min_agree
        for i in range(len(candles))
    ]
    shorts = [
        sum(1 for d in dirs_by_tf if d[i] == -1) / len(dirs_by_tf) >= cfg.mtf_min_agree
        for i in range(len(candles))
    ]
    return longs, shorts


def run_comparison(cfg: Config, coins: Optional[List[str]] = None) -> None:
    """Baseline vs multi-timeframe resonance, in-sample vs out-of-sample."""
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    coins = coins or cfg.coins
    alloc = cfg.paper_start_equity / len(coins)

    print(f"\n===== COMPARE: baseline vs MTF resonance (equal capital {alloc:,.2f}/coin) =====")
    print(f"interval={cfg.interval} mtf={cfg.mtf_timeframes} (fast={cfg.mtf_fast} slow={cfg.mtf_slow})")
    print(f"{'coin':7} | {'base full':>9} {'train':>8} {'test':>8} {'trd':>4} | "
          f"{'mtf full':>9} {'train':>8} {'test':>8} {'trd':>4}")
    print("-" * 78)

    base_total = mtf_total = 0.0
    for coin in coins:
        candles = client.get_candles(coin, cfg.interval, 5000)
        sz = client.sz_decimals(coin)
        split = int(len(candles) * 0.7)

        b_full = _backtest(candles, sz, cfg, alloc)
        b_train = _backtest(candles[:split], sz, cfg, alloc)
        b_test = _backtest(candles[split:], sz, cfg, alloc)
        base_total += b_full["final_equity"] - alloc

        longs, shorts = build_mtf_gates(candles, client, cfg, coin)
        if longs is not None:
            m_full = _backtest(candles, sz, cfg, alloc, mtf_long=longs, mtf_short=shorts)
            m_train = _backtest(candles[:split], sz, cfg, alloc, mtf_long=longs[:split], mtf_short=shorts[:split])
            m_test = _backtest(candles[split:], sz, cfg, alloc, mtf_long=longs[split:], mtf_short=shorts[split:])
            mtf_total += m_full["final_equity"] - alloc
            m_str = f"{m_full['ret_pct']:9.2f} {m_train['ret_pct']:8.2f} {m_test['ret_pct']:8.2f} {m_full['n_trades']:4}"
        else:
            m_str = "  (n/a: need more HTF candles)"
        print(
            f"{coin:7} | {b_full['ret_pct']:9.2f} {b_train['ret_pct']:8.2f} {b_test['ret_pct']:8.2f} "
            f"{b_full['n_trades']:4} | {m_str}"
        )

    print("-" * 78)
    print(f"COMBINED base return = {base_total / cfg.paper_start_equity * 100:+.2f}%  |  "
          f"MTF return = {mtf_total / cfg.paper_start_equity * 100:+.2f}%")
    print("NOTE: train=in-sample(70%), test=out-of-sample(30%). A filter helps only if it")
    print("      improves TEST, not just full-sample. Funding filter is live-only.\n")

"""Short-timeframe scalping research: Bollinger + RSI mean-reversion.

Entry: price closes back inside the band after tagging it, RSI confirms oversold/overbought.
Exits: ATR stop, TP1 at +1R (close part, move stop to breakeven), TP2 at +2R, time stop.
Tracks R-multiples (pnl / initial risk).

This is a RESEARCH tool. Short-TF strategies are fee/noise sensitive; results are
usually not reproducible live. See README for the honest disclaimer.
"""
from __future__ import annotations

import copy
from typing import Dict, List, Optional

from .client import HyperClient
from .config import Config
from .indicators import atr, bollinger, rsi
from .risk import round_size_down

SCALP_DEFAULTS = {
    "bb_period": 20,
    "bb_mult": 2.0,
    "rsi_period": 14,
    "rsi_buy": 35.0,
    "rsi_sell": 65.0,
    "stop_atr": 1.5,
    "r_mult1": 1.0,
    "r_mult2": 2.0,
    "tp1_frac": 0.5,
    "max_bars": 24,
}


def _bt(candles: List[Dict[str, float]], sz_dec: int, cfg: Config, start_equity: float, p: Dict) -> Dict:
    closes = [c["c"] for c in candles]
    _, upper, lower = bollinger(closes, p["bb_period"], p["bb_mult"])
    rsi_s = rsi(closes, p["rsi_period"])
    atr_s = atr(candles, cfg.atr_period)
    warmup = max(p["bb_period"], p["rsi_period"], cfg.atr_period) + 2

    equity = start_equity
    pos: Optional[Dict] = None
    trades: List[Dict] = []
    curve: List[float] = []
    peak = equity
    mdd = 0.0
    risk_dollars = 0.0

    def pnl_of(px: float, qty: float) -> float:
        if pos["side"] == "long":
            return (px - pos["entry"]) * qty - px * qty * cfg.fee_rate
        return (pos["entry"] - px) * qty - px * qty * cfg.fee_rate

    def close_part(px: float, qty: float, reason: str) -> None:
        nonlocal equity
        pnl = pnl_of(px, qty)
        equity += pnl
        pos["pnl"] += pnl
        pos["remaining"] -= qty
        if pos["remaining"] <= 1e-12:
            trades.append(
                {
                    "side": pos["side"],
                    "entry": pos["entry"],
                    "exit": px,
                    "size": pos["size"],
                    "pnl": pos["pnl"],
                    "r": pos["pnl"] / risk_dollars if risk_dollars > 0 else 0.0,
                    "reason": reason,
                    "t": pos.get("entry_t", 0),
                }
            )

    for i in range(len(candles)):
        bar = candles[i]

        # ---------------------------------------------------------- manage
        if pos is not None:
            side = pos["side"]
            stop = pos["stop"]
            if side == "long":
                if bar["l"] <= stop:
                    close_part(stop, pos["remaining"], "stop")
                    pos = None
                else:
                    if not pos["tp1_taken"] and bar["h"] >= pos["tp1"]:
                        q = pos["size"] * p["tp1_frac"]
                        close_part(pos["tp1"], q, "tp1")
                        pos["tp1_taken"] = True
                        pos["stop"] = pos["entry"]  # breakeven
                    if pos is not None and pos["tp1_taken"] and bar["h"] >= pos["tp2"]:
                        close_part(pos["tp2"], pos["remaining"], "tp2")
                        pos = None
            else:
                if bar["h"] >= stop:
                    close_part(stop, pos["remaining"], "stop")
                    pos = None
                else:
                    if not pos["tp1_taken"] and bar["l"] <= pos["tp1"]:
                        q = pos["size"] * p["tp1_frac"]
                        close_part(pos["tp1"], q, "tp1")
                        pos["tp1_taken"] = True
                        pos["stop"] = pos["entry"]
                    if pos is not None and pos["tp1_taken"] and bar["l"] <= pos["tp2"]:
                        close_part(pos["tp2"], pos["remaining"], "tp2")
                        pos = None
            if pos is not None:
                pos["bars"] += 1
                if pos["bars"] >= p["max_bars"]:
                    close_part(bar["c"], pos["remaining"], "time")
                    pos = None

        # ------------------------------------------------------------ entry
        if pos is None and i >= warmup and i >= 1:
            long_sig = closes[i - 1] < lower[i - 1] and closes[i] > lower[i] and rsi_s[i] < p["rsi_buy"]
            short_sig = closes[i - 1] > upper[i - 1] and closes[i] < upper[i] and rsi_s[i] > p["rsi_sell"]
            if long_sig or short_sig:
                side = "long" if long_sig else "short"
                px = bar["c"]
                stop_dist = p["stop_atr"] * atr_s[i]
                if stop_dist > 0 and px > 0 and equity > 0:
                    raw = (equity * cfg.risk_per_trade) / stop_dist
                    raw = min(raw, (equity * cfg.leverage * cfg.max_exposure) / px)
                    size = round_size_down(raw, sz_dec)
                    if size > 0 and size * px >= cfg.min_notional:
                        entry = px * (1 + cfg.slippage) if side == "long" else px * (1 - cfg.slippage)
                        sign = 1 if side == "long" else -1
                        risk_dollars = size * stop_dist
                        equity -= entry * size * cfg.fee_rate
                        pos = {
                            "side": side,
                            "size": size,
                            "remaining": size,
                            "entry": entry,
                            "stop": entry - sign * stop_dist,
                            "tp1": entry + sign * p["r_mult1"] * stop_dist,
                            "tp2": entry + sign * p["r_mult2"] * stop_dist,
                            "tp1_taken": False,
                            "bars": 0,
                            "pnl": 0.0,
                            "entry_t": bar["t"],
                        }

        curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            mdd = max(mdd, (peak - equity) / peak)

    if pos is not None:
        close_part(candles[-1]["c"], pos["remaining"], "eod")

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)
    return {
        "ret_pct": (equity - start_equity) / start_equity * 100 if start_equity else 0.0,
        "maxdd_pct": mdd * 100,
        "trades": len(trades),
        "win_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": pf,
        "avg_r": (sum(t["r"] for t in trades) / len(trades)) if trades else 0.0,
        "final_equity": equity,
    }


def run_scalp_backtest(cfg: Config, interval: Optional[str] = None, bars: int = 5000) -> None:
    data_cfg = copy.copy(cfg)
    data_cfg.mode = "paper"
    client = HyperClient(data_cfg)
    interval = interval or "15m"
    coin = cfg.coins[0]

    candles = client.get_candles(coin, interval, bars)
    sz = client.sz_decimals(coin)
    split = int(len(candles) * 0.7)
    p = dict(SCALP_DEFAULTS)

    print(f"\n===== SHORT-K SCALP BACKTEST ({coin} {interval}) =====")
    print(f"Bollinger({p['bb_period']},{p['bb_mult']}) + RSI({p['rsi_period']}) | "
          f"stop={p['stop_atr']}xATR | TP1={p['r_mult1']}R(50%) TP2={p['r_mult2']}R | max_bars={p['max_bars']}")
    print(f"{'period':10} {'ret%':>8} {'mdd%':>7} {'trades':>6} {'win%':>6} {'pf':>6} {'avgR':>6}")

    def show(name, cds):
        m = _bt(cds, sz, data_cfg, 200.0, p)
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        print(f"{name:10} {m['ret_pct']:8.2f} {m['maxdd_pct']:7.2f} {m['trades']:6} "
              f"{m['win_pct']:6.1f} {pf:>6} {m['avg_r']:6.2f}")

    show("full", candles)
    show("train", candles[:split])
    show("test", candles[split:])
    print("NOTE: short-TF results are fee/noise dominated; treat as research only.\n")

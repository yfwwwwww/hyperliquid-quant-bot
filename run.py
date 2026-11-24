"""Command-line entrypoint.

Examples:
    python -m hyperbot.run --config config.json              # run the bot
    python -m hyperbot.run --config config.json --scout      # 10s scanner + push alerts
    python -m hyperbot.run --config config.json --backtest   # backtest (train/test split)
    python -m hyperbot.run --config config.json --optimize   # parameter search (OOS-scored)
    python -m hyperbot.run --config config.json --test-notify
    python -m hyperbot.run --config config.json --list-markets
    python -m hyperbot.run --config config.json --flat       # emergency close positions
"""
from __future__ import annotations

import argparse

from .backtest import run_backtest, run_comparison
from .bot import emergency_flat, run_bot, setup_logging
from .breakout_bot import run_breakout
from .client import HyperClient
from .config import load_config
from .dashboard import run_dashboard
from .funding import run_funding_backtest, run_funding_yield
from .hf_bot import run_hf_paper
from .hf_live import run_hf_live
from .hf_report import run_hf_report
from .learn import run_learn
from .autotune import run_autotune
from .notify import make_notifier
from .optimize import run_optimize
from .report import run_report
from .scalp import run_scalp_backtest
from .scalp_paper import run_scalp_paper
from .scout import run_scout
from .scanner import run_scanner
from .smoke import run_smoke_order


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperliquid perpetual trading bot")
    parser.add_argument("--config", default=None, help="path to config JSON (default: config.json)")
    parser.add_argument("--backtest", action="store_true", help="run a backtest and exit")
    parser.add_argument("--compare", action="store_true", help="compare baseline vs MTF resonance (OOS)")
    parser.add_argument("--funding", action="store_true", help="backtest funding-carry / contrarian hypotheses (OOS)")
    parser.add_argument("--funding-yield", action="store_true", help="show trailing funding carry APR per coin")
    parser.add_argument("--scalp-backtest", action="store_true", help="backtest short-K Bollinger/RSI scalp (15m)")
    parser.add_argument("--scalp-paper", action="store_true", help="run paper-only short-K scalp with CVD gate")
    parser.add_argument("--breakout", action="store_true", help="run the Donchian breakout bot")
    parser.add_argument("--hf-paper", action="store_true", help="run the multi-signal high-frequency paper bot")
    parser.add_argument("--hf-live", action="store_true", help="run the HF bot with REAL orders")
    parser.add_argument("--hf-report", action="store_true", help="print the HF paper performance report")
    parser.add_argument("--learn", action="store_true", help="analyze the trade journal by market context")
    parser.add_argument("--autotune", action="store_true", help="learn entry filters from the journal -> policy.json")
    parser.add_argument("--dashboard", action="store_true", help="run the realtime web dashboard")
    parser.add_argument("--smoke-order", action="store_true", help="open+close a tiny real order to verify signing")
    parser.add_argument("--optimize", action="store_true", help="parameter search scored on out-of-sample data")
    parser.add_argument("--scout", action="store_true", help="run the 10s scanner and push alerts (no trading)")
    parser.add_argument("--scan", action="store_true", help="run the 10s multi-symbol feature scanner")
    parser.add_argument("--report", action="store_true", help="print a performance report from the ledger and exit")
    parser.add_argument("--test-notify", action="store_true", help="send a test notification and exit")
    parser.add_argument("--list-markets", action="store_true", help="list tradable perpetual coins and exit")
    parser.add_argument("--flat", action="store_true", help="close all positions and exit (live only)")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.test_notify:
        setup_logging(cfg.log_level)
        notifier = make_notifier(cfg)
        if not notifier.enabled:
            print("Notifications are NOT configured. Set one of:")
            print("  HYPERBOT_SERVERCHAN_KEY    (Server酱 -> 微信, 推荐)")
            print("  HYPERBOT_WECHAT_WEBHOOK    (企业微信群机器人)")
            print("  HYPERBOT_TELEGRAM_TOKEN + HYPERBOT_TELEGRAM_CHAT_ID")
            print("  HYPERBOT_WEBHOOK_URL")
            return
        notifier.send("✅ hyperbot 测试通知：推送配置正常")
        print("Test notification sent. Check your Telegram / webhook.")
        return

    if args.list_markets:
        setup_logging(cfg.log_level)
        data_cfg = load_config(args.config)
        data_cfg.mode = "paper"
        client = HyperClient(data_cfg)
        print("Tradable perpetual coins:", ", ".join(sorted(client.list_coins())))
        return

    if args.backtest:
        run_backtest(cfg)
        return
    if args.compare:
        run_comparison(cfg)
        return
    if args.funding:
        run_funding_backtest(cfg)
        return
    if args.funding_yield:
        run_funding_yield(cfg)
        return
    if args.scalp_backtest:
        run_scalp_backtest(cfg)
        return
    if args.scalp_paper:
        run_scalp_paper(cfg)
        return
    if args.smoke_order:
        run_smoke_order(cfg)
        return
    if args.optimize:
        run_optimize(cfg)
        return
    if args.scout:
        run_scout(cfg)
        return
    if args.scan:
        run_scanner(cfg)
        return
    if args.report:
        run_report(cfg)
        return
    if args.flat:
        emergency_flat(cfg)
        return
    if args.hf_live:
        run_hf_live(cfg)
        return
    if args.autotune:
        run_autotune(cfg)
        return
    if args.learn:
        run_learn(cfg)
        return
    if args.hf_report:
        run_hf_report(cfg)
        return
    if args.hf_paper:
        run_hf_paper(cfg)
        return
    if args.breakout or cfg.strategy_mode == "breakout":
        run_breakout(cfg)
        return
    if args.dashboard:
        run_dashboard(cfg)
        return
    run_bot(cfg)


if __name__ == "__main__":
    main()

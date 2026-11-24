"""Configuration loading and validation for the Hyperliquid bot."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

VALID_MODES = ("paper", "testnet", "mainnet")

# Interval string -> milliseconds. Anything else is parsed as "<n><unit>".
_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def interval_to_ms(interval: str) -> int:
    if interval in _INTERVAL_MS:
        return _INTERVAL_MS[interval]
    num = float(interval[:-1])
    unit = interval[-1].lower()
    mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]
    return int(num * mult)


@dataclass
class Config:
    # Runtime / market -----------------------------------------------------
    mode: str = "paper"  # paper | testnet | mainnet
    coins: List[str] = field(default_factory=lambda: ["BTC"])
    perp_dexs: List[str] = field(default_factory=list)  # e.g. ["xyz"] for stock/commodity perps
    interval: str = "4h"
    poll_seconds: float = 30.0
    candle_count: int = 600

    # Strategy: EMA crossover core -----------------------------------------
    strategy_mode: str = "ema_cross"  # ema_cross | breakout
    ema_fast: int = 20
    ema_slow: int = 100
    ema_trend: int = 200
    use_trend_filter: bool = True
    atr_period: int = 14
    stop_atr_multiple: float = 3.0
    take_profit_atr_multiple: float = 0.0  # 0 disables take-profit
    breakout_n: int = 55                 # Donchian entry lookback
    breakout_m: int = 10                 # Donchian exit lookback
    breakout_trend_filter: bool = False  # only take breakouts in EMA-trend direction
    breakout_trend_ema: int = 200

    # Strategy: additional filters (0 / False = disabled) -------------------
    adx_period: int = 14
    adx_min: float = 0.0             # require ADX >= this to enter (e.g. 20)
    atr_max_pct: float = 0.0         # skip if ATR/price > this (e.g. 0.05)
    momentum_period: int = 0         # ROC lookback for entry confirmation
    momentum_min: float = 0.0        # long needs ROC >= +m; short needs ROC <= -m
    funding_filter: bool = False
    funding_max_abs: float = 0.0     # abs per-hour funding above which we skip

    # Strategy: multi-timeframe resonance (live) ---------------------------
    use_mtf_filter: bool = False
    mtf_timeframes: List[str] = field(default_factory=lambda: ["1h", "4h", "1d"])
    mtf_fast: int = 20
    mtf_slow: int = 50
    mtf_min_agree: float = 0.75
    use_macd_filter: bool = False

    # Strategy: order-flow filter (live) -----------------------------------
    use_orderflow_filter: bool = False
    orderbook_levels: int = 20
    taker_window_seconds: int = 900
    orderflow_min_imb: float = 0.10
    orderflow_taker_min: float = 0.55

    # Scout mode -----------------------------------------------------------
    scout_interval: float = 10.0
    scout_ttl_seconds: int = 120
    scout_top_n: int = 5

    # Scalp mode (paper research; CVD filter is live-only) ------------------
    scalp_interval: str = "15m"
    scalp_bb_period: int = 20
    scalp_bb_mult: float = 2.0
    scalp_rsi_period: int = 14
    scalp_rsi_buy: float = 35.0
    scalp_stop_atr: float = 3.0
    scalp_r_mult1: float = 1.0
    scalp_r_mult2: float = 2.0
    scalp_tp1_frac: float = 0.5
    scalp_max_bars: int = 48
    scalp_cvd_window: int = 300
    scalp_require_cvd: bool = True
    scalp_poll_seconds: float = 10.0

    # Risk / sizing --------------------------------------------------------
    leverage: int = 3
    is_cross: bool = True
    risk_per_trade: float = 0.01      # fraction of equity risked per trade
    max_exposure: float = 0.5         # max notional = equity * leverage * this
    min_notional: float = 12.0        # skip orders below this USD notional
    slippage: float = 0.002           # live: max-fill guard; backtest: exec cost
    fee_rate: float = 0.00035         # taker fee estimate (Hyperliquid base tier)
    maker_fee_rate: float = 0.000144  # maker fee (often a rebate on HL)
    use_maker: bool = True            # maker-first entries
    maker_offset_atr: float = 0.15    # limit offset behind the market, in ATR
    maker_timeout_seconds: int = 60   # cancel unfilled maker order after this
    maker_fallback_taker: bool = True # take the market if the maker order times out
    portfolio_risk_cap_pct: float = 0.06  # sum of open stop-risk / equity
    max_trades_per_day: int = 10          # new entries per UTC day
    hf_min_score: int = 3
    hf_mtf_min: int = 2
    hf_cooldown_seconds: int = 300        # pause a coin after any exit
    notify_trades: bool = False           # push every trade (default: summary only)
    notify_interval_seconds: int = 7200   # periodic PnL summary interval

    max_daily_loss_pct: float = 0.05  # halt trading after this daily drawdown
    cooldown_seconds: int = 900       # pause after a stop-loss exit
    max_positions: int = 1            # max concurrent open positions across coins
    min_equity_stop: float = 0.0      # halt + flatten if equity <= this (0 = off)
    floor_confirm_ticks: int = 2      # require N consecutive floor reads before halting

    # Infrastructure -------------------------------------------------------
    use_websocket: bool = False       # real-time price/candle feed (live opt-in)
    use_native_tpsl: bool = False     # exchange-side TP/SL trigger orders (live)

    # Paper trading --------------------------------------------------------
    paper_start_equity: float = 10000.0

    # Misc -----------------------------------------------------------------
    log_level: str = "INFO"
    state_file: str = "state.json"
    ledger_file: str = "ledger.jsonl"
    journal_file: str = "journal_hf.jsonl"
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080
    scan_symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    scan_interval_seconds: int = 10

    # Notifications (secrets are loaded from env vars only) -----------------
    notify_enabled: bool = True
    notify_telegram_token: Optional[str] = None
    notify_telegram_chat_id: Optional[str] = None
    notify_webhook_url: Optional[str] = None
    notify_serverchan_key: Optional[str] = None      # Server酱 SendKey -> WeChat
    notify_wechat_webhook: Optional[str] = None      # WeCom group bot webhook

    # Secrets (loaded from env vars only, never from the config file) ------
    private_key: Optional[str] = None
    account_address: Optional[str] = None

    # -- helpers -----------------------------------------------------------
    def interval_ms(self) -> int:
        return interval_to_ms(self.interval)

    def warmup_bars(self) -> int:
        need = self.ema_slow
        if self.use_trend_filter:
            need = max(need, self.ema_trend)
        if self.adx_min > 0:
            need = max(need, self.adx_period + 2)
        if self.momentum_period > 0:
            need = max(need, self.momentum_period)
        return need + 2

    def validate(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {self.mode!r}")
        if self.mode in ("testnet", "mainnet"):
            if not self.private_key:
                raise ValueError(
                    f"mode={self.mode} requires HYPERLIQUID_PRIVATE_KEY to be set in the environment"
                )
            if not self.account_address:
                raise ValueError(
                    f"mode={self.mode} requires HYPERLIQUID_ACCOUNT_ADDRESS (your MAIN wallet "
                    f"address) to be set in the environment"
                )
        if not self.coins or any(not c for c in self.coins):
            raise ValueError("coins must be a non-empty list of coin names (e.g. [\"BTC\"])")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")
        if self.strategy_mode not in ("ema_cross", "breakout"):
            raise ValueError("strategy_mode must be 'ema_cross' or 'breakout'")
        if self.breakout_n < 2 or self.breakout_m < 2:
            raise ValueError("breakout_n and breakout_m must be >= 2")
        if self.breakout_trend_filter and self.breakout_trend_ema < 2:
            raise ValueError("breakout_trend_ema must be >= 2")
        if self.ema_fast < 2 or self.atr_period < 2:
            raise ValueError("ema_fast and atr_period must be >= 2")
        if self.adx_min < 0 or self.atr_max_pct < 0 or self.momentum_period < 0 or self.funding_max_abs < 0:
            raise ValueError("adx_min/atr_max_pct/momentum_period/funding_max_abs must be >= 0")
        if self.leverage < 1:
            raise ValueError("leverage must be >= 1")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.min_equity_stop < 0:
            raise ValueError("min_equity_stop must be >= 0")
        if self.floor_confirm_ticks < 1:
            raise ValueError("floor_confirm_ticks must be >= 1")
        if not (0 < self.risk_per_trade <= 1):
            raise ValueError("risk_per_trade must be in (0, 1]")
        if not (0 < self.max_exposure <= 1):
            raise ValueError("max_exposure must be in (0, 1]")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple must be > 0")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        if not self.mtf_timeframes:
            raise ValueError("mtf_timeframes must be non-empty")
        if not (0 < self.mtf_min_agree <= 1):
            raise ValueError("mtf_min_agree must be in (0, 1]")
        if self.orderbook_levels < 1 or self.taker_window_seconds <= 0:
            raise ValueError("orderbook_levels and taker_window_seconds must be positive")
        if not (0 <= self.orderflow_min_imb <= 1):
            raise ValueError("orderflow_min_imb must be in [0, 1]")
        if not (0.5 <= self.orderflow_taker_min <= 1):
            raise ValueError("orderflow_taker_min must be in [0.5, 1]")
        if self.scout_interval <= 0 or self.scout_ttl_seconds <= 0:
            raise ValueError("scout_interval and scout_ttl_seconds must be > 0")
        if self.candle_count < self.warmup_bars():
            raise ValueError(
                f"candle_count ({self.candle_count}) is too small; needs at least "
                f"{self.warmup_bars()} candles to warm up indicators"
            )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("private_key", None)
        d.pop("account_address", None)
        return d


def _read_env_secrets(cfg: Config) -> Config:
    cfg.private_key = os.environ.get("HYPERLIQUID_PRIVATE_KEY") or None
    cfg.account_address = os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS") or None
    cfg.notify_telegram_token = os.environ.get("HYPERBOT_TELEGRAM_TOKEN") or None
    cfg.notify_telegram_chat_id = os.environ.get("HYPERBOT_TELEGRAM_CHAT_ID") or None
    cfg.notify_webhook_url = os.environ.get("HYPERBOT_WEBHOOK_URL") or None
    cfg.notify_serverchan_key = os.environ.get("HYPERBOT_SERVERCHAN_KEY") or None
    cfg.notify_wechat_webhook = os.environ.get("HYPERBOT_WECHAT_WEBHOOK") or None
    mode = os.environ.get("HYPERBOT_MODE")
    if mode:
        cfg.mode = mode
    return cfg


def load_config(path: Optional[str] = None) -> Config:
    """Load config from a JSON file (default: config.json) and merge env secrets."""
    path = path or os.environ.get("HYPERBOT_CONFIG", "config.json")
    cfg = Config()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        known = {f.name for f in fields(Config)}
        for key, value in raw.items():
            if key in known and key not in ("private_key", "account_address"):
                setattr(cfg, key, value)
    cfg = _read_env_secrets(cfg)
    cfg.validate()
    return cfg

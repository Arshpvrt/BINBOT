"""Centralized, typed configuration for the ATS.

All runtime tunables live here so that risk limits and connection
parameters are never hardcoded deep in business logic. Values are
sourced from environment variables / a .env file, with conservative
defaults suitable for a paper-trading account.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BinanceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BINANCE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # SecretStr keeps the key/secret out of logs, tracebacks, and repr() —
    # str(settings) or logger.info("settings", **settings) will never leak
    # the raw value. Call .get_secret_value() only at the point of use.
    api_key: SecretStr = SecretStr("")
    api_secret: SecretStr = SecretStr("")

    # Defaults to the PRACTICE exchange (fake funds, real exchange behavior).
    # Flipping this to False routes every order to your REAL account —
    # never change it without deliberately meaning to.
    testnet: bool = True

    recv_window_ms: int = 5000
    reconnect_backoff_s: float = 2.0
    reconnect_backoff_max_s: float = 60.0
    user_stream_keepalive_s: float = 1800.0  # Binance listenKey expires after 60 min idle


class IBKRSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IBKR_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 7497  # 7497 = TWS paper, 7496 = TWS live, 4002/4001 = IB Gateway
    client_id: int = 1
    account: str = ""
    read_only: bool = True
    connect_timeout_s: float = 10.0
    reconnect_backoff_s: float = 2.0
    reconnect_backoff_max_s: float = 60.0
    heartbeat_interval_s: float = 5.0
    heartbeat_timeout_s: float = 15.0


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RISK_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    max_daily_drawdown_pct: float = 2.0
    max_position_notional_usd: float = 250_000.0
    max_contracts_per_order: int = 20
    max_contracts_per_symbol: int = 50
    max_orders_per_second: float = 5.0
    max_orders_per_minute: int = 60
    target_annual_volatility_pct: float = 12.0
    kelly_fraction_cap: float = 0.5
    initial_margin_buffer_pct: float = 25.0
    maintenance_margin_buffer_pct: float = 10.0
    max_gross_leverage: float = 4.0

    @field_validator("max_daily_drawdown_pct")
    @classmethod
    def _validate_drawdown(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("max_daily_drawdown_pct must be in (0, 100]")
        return v


class DataSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    duckdb_path: str = "./data_store/market_data.duckdb"
    redis_url: str = "redis://localhost:6379/0"
    tick_channel_prefix: str = "ticks"
    bar_channel_prefix: str = "bars"
    execution_channel: str = "executions"


class ScannerStrategySettings(BaseSettings):
    """Funding-momentum scanner: watches every USDT-margined Binance
    Futures perpetual for a large price move confirmed by a funding-rate
    trend, rather than trading one fixed pair."""

    model_config = SettingsConfigDict(
        env_prefix="SCANNER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    max_open_positions: int = 4
    leverage: int = 15
    # Order margin and stop-loss are both a % of the LIVE futures account
    # balance rather than a flat dollar amount — margin is read fresh every
    # time a new entry signal fires (see FundingMomentumScannerStrategy.
    # on_bar); stop-loss is a snapshot taken once, the first time a
    # position is seen (see PositionMonitor._check_once), and stays fixed
    # for that position's lifetime rather than recalculating while it's
    # held — same lifecycle the old flat $50/$200 values already had, just
    # sourced from live equity instead of a config constant.
    margin_equity_pct: float = 15.0
    stop_loss_equity_pct: float = 25.0
    # Cross margin (not the ISOLATED default elsewhere): a per-position
    # stop-loss larger than that position's own isolated margin can only
    # ever be enforced in software if losses can draw from the whole
    # account, not just the margin backing this one position.
    margin_type: str = "CROSSED"

    # Profit exit is a trailing stop, not a fixed target: once unrealized
    # ROI (against the position's actual margin) first reaches
    # trailing_profit_arm_roi_pct,
    # a stop arms at trailing_profit_base_roi_pct and rises
    # trailing_profit_step_roi_pct for every additional
    # trailing_profit_step_increment_roi_pct of peak profit reached after
    # that — e.g. with the defaults below: arms at 30%, trails at 20%,
    # rising to 22% once peak profit hits 35%, 24% at 40%, and so on. The
    # position closes when ROI pulls back down to whatever that trailing
    # level currently is, letting a strong move keep running instead of
    # capping every winner at the same fixed target. See
    # execution/position_monitor.py for the exact formula.
    trailing_profit_arm_roi_pct: float = 30.0
    trailing_profit_base_roi_pct: float = 20.0
    trailing_profit_step_roi_pct: float = 2.0
    trailing_profit_step_increment_roi_pct: float = 5.0
    # Unconditional ceiling: closes the instant ROI reaches this, even
    # without waiting for a pullback — a hard cap on top of the trailing
    # stop above, so an exceptional move doesn't ride all the way back
    # down to a (by then, much higher) trailing level before exiting.
    trailing_profit_hard_cap_roi_pct: float = 80.0

    price_jump_pct: float = 10.0
    price_jump_window_min: int = 20
    funding_trend_window_min: int = 20

    position_monitor_interval_s: float = 2.0
    universe_refresh_interval_s: float = 3600.0  # re-fetch the symbol list hourly

    @field_validator("leverage")
    @classmethod
    def _validate_leverage(cls, v: int) -> int:
        if not (1 <= v <= 125):
            raise ValueError("leverage must be in [1, 125]")
        return v

    @field_validator("max_open_positions")
    @classmethod
    def _validate_max_positions(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_open_positions must be at least 1")
        return v

    @field_validator("margin_equity_pct", "stop_loss_equity_pct")
    @classmethod
    def _validate_equity_pct(cls, v: float) -> float:
        if not (0 < v <= 100):
            raise ValueError("margin_equity_pct and stop_loss_equity_pct must be in (0, 100]")
        return v

    @field_validator("trailing_profit_step_increment_roi_pct")
    @classmethod
    def _validate_step_increment(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("trailing_profit_step_increment_roi_pct must be positive")
        return v

    @model_validator(mode="after")
    def _validate_trailing_base_below_arm(self) -> "ScannerStrategySettings":
        if self.trailing_profit_base_roi_pct > self.trailing_profit_arm_roi_pct:
            raise ValueError("trailing_profit_base_roi_pct must not exceed trailing_profit_arm_roi_pct")
        return self

    @model_validator(mode="after")
    def _validate_hard_cap_above_arm(self) -> "ScannerStrategySettings":
        if self.trailing_profit_hard_cap_roi_pct <= self.trailing_profit_arm_roi_pct:
            raise ValueError("trailing_profit_hard_cap_roi_pct must exceed trailing_profit_arm_roi_pct")
        return self


class TelegramSettings(BaseSettings):
    """Optional proactive alerts via a Telegram bot: trade opened/closed
    (with P&L), stop-loss/take-profit triggers, and risk halts. Leave
    bot_token blank to disable — nothing else in the bot changes."""

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "paper"  # paper | live | backtest
    log_level: str = "INFO"
    log_json: bool = True

    ibkr: IBKRSettings = Field(default_factory=IBKRSettings)
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    scanner: ScannerStrategySettings = Field(default_factory=ScannerStrategySettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)

    @field_validator("env")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        allowed = {"paper", "live", "backtest"}
        if v not in allowed:
            raise ValueError(f"env must be one of {allowed}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

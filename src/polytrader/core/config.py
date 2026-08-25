"""Central configuration.

All risk limits live here with the defaults from the PRD's Risk Management
Specification. Nothing in the risk engine hardcodes a number -- everything
is read from a Settings instance so limits are auditable and overridable
via environment variables (see .env.example).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./polytrader.db"

    anthropic_api_key: str | None = None
    research_model: str = "claude-sonnet-5"

    # --- Risk engine limits (PRD 1 section 7) ---
    max_risk_per_trade_pct: float = 0.02
    kelly_fraction: float = 0.35
    max_total_exposure_pct: float = 0.50
    max_market_concentration_pct: float = 0.08
    max_category_concentration_pct: float = 0.25
    daily_loss_pause_pct: float = 0.05
    max_drawdown_halt_pct: float = 0.18
    min_adjusted_edge: float = 0.03
    probability_extreme_guard: float = 0.90
    max_data_staleness_seconds: int = 900
    thesis_cooldown_hours: int = 24
    kill_switch: bool = False

    # --- Bankrolls (paper) ---
    mirror_starting_bankroll: float = 100_000.0
    polymarket_starting_bankroll: float = 10_000.0

    # --- Trading friction assumptions (edge calculator defaults) ---
    default_fee_bps: float = 200.0
    default_slippage_bps: float = 100.0
    default_uncertainty_buffer: float = 0.02

    # --- Live trading gate: stays hard-disabled for this build ---
    live_trading_enabled: bool = False


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

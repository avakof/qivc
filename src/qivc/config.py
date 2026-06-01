from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QIVC_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Required
    edgar_user_agent: str = Field(..., description="Format: 'Name email@domain.com'")

    # Optional auth
    fmp_api_key: str | None = Field(default=None)

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # Storage
    db_path: str = Field(default="data/duckdb/qivc.db")
    output_dir: str = Field(default="data/runs")

    # EDGAR tuning
    edgar_rate_limit_rps: int = Field(default=8)
    form4_lookback_days: int = Field(default=14)

    # CMP classifier
    cmp_history_years: int = Field(default=3)

    # Cluster detection
    cluster_window_days: int = Field(default=7)

    # Liquidity
    min_market_cap_usd: int = Field(default=300_000_000)

    # yfinance
    yfinance_retry_count: int = Field(default=3)

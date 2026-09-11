"""Engine settings — loaded from environment / .env (see .env.example). Keys never leave the server."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    tradehub_env: str = "dev"
    tradehub_tz: str = "America/New_York"
    paper_mode: bool = True

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""

    alpaca_paper_key_id: str = ""
    alpaca_paper_secret_key: str = ""
    alpaca_paper_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_base_url: str = "https://data.alpaca.markets"
    alpaca_data_feed: str = "iex"

    finviz_elite_auth_token: str = ""
    fmp_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "tradehub/0.1"
    finnhub_api_key: str = ""
    firecrawl_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "hermes3:8b"
    openrouter_api_key: str = ""
    openrouter_model_judgment: str = "nousresearch/hermes-3-llama-3.1-70b"
    openrouter_model_high_stakes: str = "nousresearch/hermes-3-llama-3.1-405b"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    n8n_webhook_secret: str = ""
    engine_api_token: str = ""
    fred_api_key: str = ""
    eia_api_key: str = ""
    twelvedata_api_key: str = ""

    engine_host: str = "0.0.0.0"
    engine_port: int = 8000
    parquet_store_path: Path = REPO_ROOT / "data" / "parquet"
    prompt_library_path: Path = REPO_ROOT / "prompt-library"
    log_level: str = "INFO"

    @field_validator("parquet_store_path", "prompt_library_path", mode="after")
    @classmethod
    def _anchor_to_repo(cls, v: Path) -> Path:
        """A relative path in .env must not mean "wherever the caller happened to be".

        `PARQUET_STORE_PATH=./data/parquet` resolved against the current working directory, so the same
        setting pointed at `<repo>/data/parquet` from the repo root and at `<repo>/engine/data/parquet`
        from inside `engine/` — which is where every CLI run is started. The container and the host
        therefore kept SEPARATE bar caches without either of them being wrong, and a backtest triggered
        by n8n re-downloaded everything the owner had already cached. Anchoring to the repo root makes
        the setting mean one directory.
        """
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_paper_key_id and self.alpaca_paper_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

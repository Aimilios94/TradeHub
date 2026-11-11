"""Settings read from environment variables (see .env.example at the repo root)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_user: str = "tradehub"
    postgres_password: str = ""
    postgres_db: str = "tradehub"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    searxng_base_url: str = "http://searxng:8080"
    ollama_base_url: str = "http://host.docker.internal:11434"
    n8n_trigger_secret: str = ""

    finnhub_api_key: str = ""
    massive_api_key: str = ""

    openrouter_api_key: str = ""
    openrouter_model_better: str = "nousresearch/hermes-4-405b"
    openrouter_model_cheap: str = ""
    ollama_model: str = ""
    llm_monthly_budget_eur: float = 5.0

    seeds_dir: Path = REPO_ROOT / "db" / "seeds"
    # Parquet recordings and frozen snapshots. Git-ignored; mounted from the host in Compose.
    data_dir: Path = REPO_ROOT / "data"

    def database_url(self, database: str | None = None) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=database or self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

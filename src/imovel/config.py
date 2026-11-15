"""Application settings, loaded from environment variables and an optional ``.env`` file."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{DATA_DIR / 'imovel.db'}"

    admin_user: str = "admin"
    admin_password: str = "admin"
    admin_secret_key: str = "insecure-development-key"

    criteria_min_price: int = 100_000
    criteria_max_price: int = 600_000

    rate_limit_seconds: float = 2.0
    max_pages_per_source: int = 15
    max_items_per_run: int = 1500
    request_timeout: float = 40.0
    max_retries: int = 3

    tz: str = "America/Sao_Paulo"
    log_level: str = "INFO"

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    @property
    def default_min_price(self) -> Decimal:
        return Decimal(self.criteria_min_price)

    @property
    def default_max_price(self) -> Decimal:
        return Decimal(self.criteria_max_price)


def get_settings() -> Settings:
    return Settings()

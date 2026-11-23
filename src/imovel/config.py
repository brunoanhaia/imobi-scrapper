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

    rate_limit_seconds: float = 3.0
    max_pages_per_source: int = 90
    max_items_per_run: int = 1500
    request_timeout: float = 40.0
    max_retries: int = 3

    tz: str = "America/Sao_Paulo"
    log_level: str = "INFO"

    # Reverse proxy / public deployment.
    session_https_only: bool = False
    allowed_hosts: str = ""

    # On-demand backup page: dumps are downloaded by the browser and restores are uploaded.
    backup_enabled: bool = True
    backup_dir: str = "data/backups"
    backup_max_upload_mb: int = 512
    backup_timeout_seconds: int = 300
    state_dir: str = "data"

    # On-demand image proxy (portals block hotlinking and some CDNs must be size-rewritten).
    images_allowed_hosts: tuple[str, ...] = (
        "cdn.chavesnamao.com.br",
        "chavesnamao.com.br",
        "img.kenlo.io",
        "imgs.kenlo.io",
        "cdn.imoview.com.br",
        "resizedimgs.vivareal.com",
        "resizedimgs.zapimoveis.com.br",
        "img.olx.com.br",
        "images.olx.com.br",
        "imgbr.imovelwebcdn.com",
        "http2.mlstatic.com",
        "imovelweb.com.br",
    )
    images_cache_dir: str = "data/img-cache"
    images_cache_max_mb: int = 2048
    images_max_bytes: int = 8 * 1024 * 1024
    images_negative_ttl_seconds: int = 86400


    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    @property
    def allowed_host_list(self) -> list[str]:
        hosts = [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]
        # The container healthcheck talks to localhost with a Host header that is not the
        # public domain; accept it without weakening the domain restriction.
        return [*hosts, "localhost", "127.0.0.1"] if hosts else []

    @property
    def default_min_price(self) -> Decimal:
        return Decimal(self.criteria_min_price)

    @property
    def default_max_price(self) -> Decimal:
        return Decimal(self.criteria_max_price)


def get_settings() -> Settings:
    return Settings()

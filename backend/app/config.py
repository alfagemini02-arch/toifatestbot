from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Telegram Test Mini App"
    debug: bool = False

    bot_token: str = ""
    bot_username: str = ""
    admin_ids: CsvList = Field(default_factory=list)
    admin_username: str = ""
    webhook_secret: str = ""
    webapp_url: str = "http://localhost:10000"

    secret_key: str = "development-only-change-me"
    access_token_minutes: int = 1440
    admin_token_hours: int = 8
    telegram_auth_max_age_seconds: int = 86400
    webapp_login_token_hours: int = 168

    database_url: str = "sqlite:///./data/app.db"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "ChangeMe_12345"

    allowed_origins: CsvList = Field(default_factory=lambda: ["http://localhost:10000"])
    trusted_hosts: CsvList = Field(default_factory=lambda: ["localhost", "127.0.0.1", "*.onrender.com"])
    max_upload_mb: int = 20
    rate_limit_per_minute: int = 60

    enable_dev_auth: bool = False
    dev_telegram_id: int = 999000111

    @property
    def admin_id_set(self) -> set[int]:
        result: set[int] = set()
        for value in self.admin_ids:
            try:
                result.add(int(value))
            except (TypeError, ValueError):
                continue
        return result

    @property
    def normalized_webapp_url(self) -> str:
        return self.webapp_url.rstrip("/")

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def local_data_dir(self) -> Path:
        path = Path("data")
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

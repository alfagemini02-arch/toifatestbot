from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _normalize_webhook_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if not url.startswith(("https://", "http://")):
        return "https://" + url
    return url


def _normalize_webhook_path(value: str) -> str:
    path = value.strip() or "/webhook"
    if not path.startswith("/"):
        path = "/" + path
    return path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_mode: str
    webhook_url: str
    webhook_path: str
    web_host: str
    web_port: int
    data_source: str
    database_path: Path
    user_database_url: str
    terms_image_path: Path
    terms_photo_file_id: str
    terms_pdf_path: Path
    permission_rules_path: Path
    fees_rules_path: Path
    bhm_value: int
    usd_fallback_rate: float
    admin_username: str
    admin_password: str
    admin_session_secret: str
    sql_connection_string: str
    sql_query_path: Path
    timezone: str = "Asia/Tashkent"


def get_settings() -> Settings:
    webhook_url = _normalize_webhook_url(os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "")
    bot_mode = os.getenv("BOT_MODE", "polling").strip().lower()

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        bot_mode=bot_mode,
        webhook_url=webhook_url,
        webhook_path=_normalize_webhook_path(os.getenv("WEBHOOK_PATH", "/webhook")),
        web_host=os.getenv("WEB_SERVER_HOST", "0.0.0.0").strip(),
        web_port=int(os.getenv("PORT", "8080")),
        data_source=os.getenv("DATA_SOURCE", "demo").strip().lower(),
        database_path=BASE_DIR / os.getenv("DATABASE_PATH", "data/bot_data.sqlite3"),
        user_database_url=(os.getenv("USER_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip(),
        terms_image_path=BASE_DIR / os.getenv("TERMS_IMAGE_PATH", "assets/foydalanish_shartlari.png"),
        terms_photo_file_id=os.getenv("TERMS_PHOTO_FILE_ID", "").strip(),
        terms_pdf_path=BASE_DIR / os.getenv("TERMS_PDF_PATH", "assets/foydalanish_shartlari.pdf"),
        permission_rules_path=BASE_DIR / os.getenv("PERMISSION_RULES_PATH", "data/permission_rules.json"),
        fees_rules_path=BASE_DIR / os.getenv("FEES_RULES_PATH", "data/fees_2026.json"),
        bhm_value=int(os.getenv("BHM_VALUE", "412000")),
        usd_fallback_rate=float(os.getenv("USD_FALLBACK_RATE", "12600")),
        admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", "nazorat2026").strip(),
        admin_session_secret=os.getenv("ADMIN_SESSION_SECRET", "change-this-admin-secret").strip(),
        sql_connection_string=os.getenv("SQL_CONNECTION_STRING", "").strip(),
        sql_query_path=BASE_DIR / os.getenv(
            "SQL_QUERY_PATH",
            "sql/vehicle_check.sql",
        ),
        timezone=os.getenv("TZ", "Asia/Tashkent").strip(),
    )

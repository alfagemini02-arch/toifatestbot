from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl

import jwt
from fastapi import HTTPException, status
from passlib.context import CryptContext

from .config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, role: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_webapp_login_token(telegram_id: int, expires_delta: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    expires = expires_delta or timedelta(hours=settings.webapp_login_token_hours)
    payload = {
        "sub": str(telegram_id),
        "telegram_id": telegram_id,
        "role": "webapp-login",
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str, expected_role: str | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token yaroqsiz yoki muddati tugagan") from exc
    if expected_role and payload.get("role") != expected_role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat yetarli emas")
    return payload


def validate_webapp_login_token(token: str) -> int:
    payload = decode_access_token(token, expected_role="webapp-login")
    try:
        return int(payload.get("telegram_id") or payload["sub"])
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mini App login tokeni yaroqsiz") from exc


def validate_telegram_init_data(init_data: str, max_age_seconds: int | None = None) -> dict[str, Any]:
    if not settings.bot_token:
        raise HTTPException(status_code=503, detail="BOT_TOKEN sozlanmagan")
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram initData mavjud emas")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram imzosi mavjud emas")

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Telegram initData imzosi noto'g'ri")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="auth_date noto'g'ri") from exc

    age = int(datetime.now(timezone.utc).timestamp()) - auth_date
    allowed_age = max_age_seconds or settings.telegram_auth_max_age_seconds
    if age < -60 or age > allowed_age:
        raise HTTPException(status_code=401, detail="Telegram initData muddati o'tgan")

    result: dict[str, Any] = dict(pairs)
    for key in ("user", "receiver", "chat"):
        if key in result:
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=401, detail=f"Telegram {key} ma'lumoti buzilgan") from exc
    return result

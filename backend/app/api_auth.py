from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Admin, User
from .schemas import AdminLoginRequest, DevAuthRequest, TelegramAuthRequest
from .security import create_access_token, validate_telegram_init_data, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()
_login_failures: dict[str, deque[datetime]] = defaultdict(deque)


def _check_login_throttle(key: str) -> None:
    now = datetime.now(timezone.utc)
    queue = _login_failures[key]
    while queue and queue[0] < now - timedelta(minutes=1):
        queue.popleft()
    if len(queue) >= 3:
        raise HTTPException(status_code=429, detail="3 marta noto'g'ri urinish. 1 daqiqadan so'ng qayta urinib ko'ring")


@router.post("/telegram")
def telegram_auth(payload: TelegramAuthRequest, db: Session = Depends(get_db)) -> dict:
    data = validate_telegram_init_data(payload.init_data)
    tg_user = data.get("user") or {}
    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Telegram foydalanuvchisi aniqlanmadi")
    user = db.scalar(select(User).where(User.telegram_id == int(telegram_id)))
    if not user:
        raise HTTPException(status_code=403, detail="Iltimos, avval botda /start orqali ro'yxatdan o'ting")
    user.username = tg_user.get("username") or user.username
    user.last_active_at = datetime.now(timezone.utc)
    db.commit()
    token = create_access_token(str(user.id), "user", timedelta(minutes=settings.access_token_minutes))
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "telegram_id": user.telegram_id, "full_name": user.full_name, "username": user.username},
    }


@router.post("/dev")
def dev_auth(payload: DevAuthRequest, db: Session = Depends(get_db)) -> dict:
    if not settings.enable_dev_auth:
        raise HTTPException(status_code=404, detail="Topilmadi")
    telegram_id = payload.telegram_id or settings.dev_telegram_id
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        user = User(telegram_id=telegram_id, full_name=payload.full_name, phone=payload.phone, username="dev_user")
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(str(user.id), "user", timedelta(minutes=settings.access_token_minutes))
    return {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "full_name": user.full_name}}


@router.post("/admin/login")
def admin_login(payload: AdminLoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    client = request.client.host if request.client else "unknown"
    key = f"{client}:{payload.username.casefold()}"
    _check_login_throttle(key)

    admin = db.scalar(select(Admin).where(Admin.username == payload.username.strip()))
    if not admin or not verify_password(payload.password, admin.password_hash):
        _login_failures[key].append(datetime.now(timezone.utc))
        raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")
    _login_failures.pop(key, None)
    token = create_access_token(str(admin.id), "admin", timedelta(hours=settings.admin_token_hours))
    return {"access_token": token, "token_type": "bearer", "admin": {"id": admin.id, "username": admin.username}}

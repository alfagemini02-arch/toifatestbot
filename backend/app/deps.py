from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Admin, User
from .security import decode_access_token

settings = get_settings()
bearer = HTTPBearer(auto_error=False)


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=window_seconds)
        with self._lock:
            queue = self._hits[key]
            while queue and queue[0] < cutoff:
                queue.popleft()
            if len(queue) >= limit:
                raise HTTPException(status_code=429, detail="Juda ko'p so'rov. Birozdan so'ng qayta urinib ko'ring")
            queue.append(now)
            if len(self._hits) > 10_000:
                stale_keys = [item_key for item_key, hits in self._hits.items() if not hits or hits[-1] < cutoff]
                for item_key in stale_keys[:2_000]:
                    self._hits.pop(item_key, None)


rate_limiter = InMemoryRateLimiter()


def enforce_rate_limit(request: Request) -> None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        identity = hashlib.sha256(authorization.encode()).hexdigest()[:20]
        key = f"token:{identity}"
    else:
        key = f"ip:{request.client.host if request.client else 'unknown'}"
    rate_limiter.check(key, settings.rate_limit_per_minute)


def _token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Avtorizatsiya talab etiladi")
    return credentials.credentials


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_access_token(_token(credentials), expected_role="user")
    user = db.scalar(select(User).where(User.id == int(payload["sub"])))
    if not user or user.is_blocked:
        raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi yoki bloklangan")
    now = datetime.now(timezone.utc)
    last_active = user.last_active_at
    if last_active is None or (now - (last_active if last_active.tzinfo else last_active.replace(tzinfo=timezone.utc))) >= timedelta(minutes=5):
        user.last_active_at = now
        db.commit()
    return user


def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Admin:
    payload = decode_access_token(_token(credentials), expected_role="admin")
    admin = db.scalar(select(Admin).where(Admin.id == int(payload["sub"])))
    if not admin:
        raise HTTPException(status_code=401, detail="Admin topilmadi")
    return admin

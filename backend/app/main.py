from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import ORJSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .api_admin import router as admin_router
from .api_auth import router as auth_router
from .api_user import router as user_router
from .bot import process_update, setup_bot, shutdown_bot
from .config import get_settings
from .database import SessionLocal
from .seed import initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201, ARG001
    initialize_database()
    await setup_bot()
    yield
    await shutdown_bot()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    default_response_class=ORJSONResponse,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    lifespan=lifespan,
)

origins = settings.allowed_origins or [settings.normalized_webapp_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Telegram-Bot-Api-Secret-Token"],
)
trusted_hosts = settings.trusted_hosts or ["*"]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)


@app.get("/api/health")
def health() -> dict:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "service": settings.app_name}


@app.post("/api/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if not settings.bot_token:
        raise HTTPException(status_code=503, detail="Bot sozlanmagan")
    if not settings.webhook_secret or x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Webhook secret noto'g'ri")
    payload = await request.json()
    await process_update(payload)
    return {"ok": True}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


static_root = Path("static")
user_static = static_root / "user"
admin_static = static_root / "admin"

if user_static.exists():
    app.mount("/app", StaticFiles(directory=user_static, html=True), name="user-app")
else:
    logger.warning("User frontend build topilmadi: %s", user_static)

if admin_static.exists():
    app.mount("/admin", StaticFiles(directory=admin_static, html=True), name="admin-app")
else:
    logger.warning("Admin frontend build topilmadi: %s", admin_static)

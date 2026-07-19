from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = Path('/tmp/telegram_test_miniapp_pytest.db')
TEST_DB.unlink(missing_ok=True)

os.environ.setdefault('DATABASE_URL', f'sqlite:///{TEST_DB}')
os.environ.setdefault('BOT_TOKEN', '123456:TEST_TOKEN_FOR_UNIT_TESTS')
os.environ.setdefault('WEBAPP_URL', 'http://localhost:10000')
os.environ.setdefault('WEBHOOK_SECRET', 'unit-test-secret')
os.environ.setdefault('SECRET_KEY', 'unit-test-secret-key-that-is-long-enough')
os.environ.setdefault('TRUSTED_HOSTS', 'testserver,localhost,127.0.0.1')
os.environ.setdefault('ALLOWED_ORIGINS', 'http://localhost:10000')
os.environ.setdefault('BOOTSTRAP_ADMIN_USERNAME', 'admin')
os.environ.setdefault('BOOTSTRAP_ADMIN_PASSWORD', 'Admin_Test_12345')
os.environ.setdefault('ENABLE_DEV_AUTH', 'true')

from backend.app.database import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

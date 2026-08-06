from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiosqlite


@dataclass
class UserProfile:
    user_id: int
    full_name: str | None
    phone: str | None
    terms_accepted_at: str | None
    language_code: str | None = None

    @property
    def is_registered(self) -> bool:
        return bool(self.language_code and self.full_name and self.phone and self.terms_accepted_at)


class UserStorage(Protocol):
    async def init(self) -> None: ...

    async def close(self) -> None: ...

    async def upsert_telegram_user(self, user_id: int, username: str | None, telegram_full_name: str | None) -> None: ...

    async def set_full_name(self, user_id: int, full_name: str) -> None: ...

    async def set_phone(self, user_id: int, phone: str) -> None: ...

    async def set_language(self, user_id: int, language_code: str) -> None: ...

    async def accept_terms(self, user_id: int) -> str: ...

    async def get_profile(self, user_id: int) -> UserProfile | None: ...


class TimezoneMixin:
    def __init__(self, timezone: str = "Asia/Tashkent") -> None:
        self.timezone = self._load_timezone(timezone)

    def now_text(self) -> str:
        return datetime.now(self.timezone).isoformat(timespec="seconds")

    @staticmethod
    def _load_timezone(timezone: str):
        try:
            return ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            return datetime_timezone(timedelta(hours=5))


class SQLiteUserStorage(TimezoneMixin):
    def __init__(self, database_path: Path, timezone: str = "Asia/Tashkent") -> None:
        super().__init__(timezone)
        self.database_path = database_path

    async def init(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    telegram_full_name TEXT,
                    full_name TEXT,
                    phone TEXT,
                    terms_accepted_at TEXT,
                    language_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await self._ensure_column(db, "language_code")
            await db.commit()

    async def _ensure_column(self, db: aiosqlite.Connection, column_name: str) -> None:
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if column_name not in columns:
            await db.execute(f"ALTER TABLE users ADD COLUMN {column_name} TEXT")

    async def close(self) -> None:
        return None

    async def upsert_telegram_user(self, user_id: int, username: str | None, telegram_full_name: str | None) -> None:
        now = self.now_text()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, telegram_full_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    telegram_full_name = excluded.telegram_full_name,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, telegram_full_name, now, now),
            )
            await db.commit()

    async def set_full_name(self, user_id: int, full_name: str) -> None:
        now = self.now_text()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, full_name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = excluded.full_name,
                    updated_at = excluded.updated_at
                """,
                (user_id, full_name.strip(), now, now),
            )
            await db.commit()

    async def set_phone(self, user_id: int, phone: str) -> None:
        now = self.now_text()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, phone, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    phone = excluded.phone,
                    updated_at = excluded.updated_at
                """,
                (user_id, phone.strip(), now, now),
            )
            await db.commit()

    async def set_language(self, user_id: int, language_code: str) -> None:
        now = self.now_text()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, language_code, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    language_code = excluded.language_code,
                    updated_at = excluded.updated_at
                """,
                (user_id, language_code, now, now),
            )
            await db.commit()

    async def accept_terms(self, user_id: int) -> str:
        accepted_at = self.now_text()
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, terms_accepted_at, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    terms_accepted_at = excluded.terms_accepted_at,
                    updated_at = excluded.updated_at
                """,
                (user_id, accepted_at, accepted_at, accepted_at),
            )
            await db.commit()
        return accepted_at

    async def get_profile(self, user_id: int) -> UserProfile | None:
        async with aiosqlite.connect(self.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id, full_name, phone, terms_accepted_at, language_code FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return UserProfile(
            user_id=row["user_id"],
            full_name=row["full_name"],
            phone=row["phone"],
            terms_accepted_at=row["terms_accepted_at"],
            language_code=row["language_code"],
        )


class PostgresUserStorage(TimezoneMixin):
    def __init__(self, database_url: str, timezone: str = "Asia/Tashkent") -> None:
        super().__init__(timezone)
        self.database_url = database_url
        self.pool = None

    async def init(self) -> None:
        import asyncpg

        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=3)
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    telegram_full_name TEXT,
                    full_name TEXT,
                    phone TEXT,
                    terms_accepted_at TEXT,
                    language_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS language_code TEXT")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def upsert_telegram_user(self, user_id: int, username: str | None, telegram_full_name: str | None) -> None:
        now = self.now_text()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (user_id, username, telegram_full_name, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    telegram_full_name = EXCLUDED.telegram_full_name,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                username,
                telegram_full_name,
                now,
                now,
            )

    async def set_full_name(self, user_id: int, full_name: str) -> None:
        now = self.now_text()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (user_id, full_name, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                full_name.strip(),
                now,
                now,
            )

    async def set_phone(self, user_id: int, phone: str) -> None:
        now = self.now_text()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (user_id, phone, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(user_id) DO UPDATE SET
                    phone = EXCLUDED.phone,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                phone.strip(),
                now,
                now,
            )

    async def set_language(self, user_id: int, language_code: str) -> None:
        now = self.now_text()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (user_id, language_code, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(user_id) DO UPDATE SET
                    language_code = EXCLUDED.language_code,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                language_code,
                now,
                now,
            )

    async def accept_terms(self, user_id: int) -> str:
        accepted_at = self.now_text()
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (user_id, terms_accepted_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT(user_id) DO UPDATE SET
                    terms_accepted_at = EXCLUDED.terms_accepted_at,
                    updated_at = EXCLUDED.updated_at
                """,
                user_id,
                accepted_at,
                accepted_at,
                accepted_at,
            )
        return accepted_at

    async def get_profile(self, user_id: int) -> UserProfile | None:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT user_id, full_name, phone, terms_accepted_at, language_code FROM users WHERE user_id = $1",
                user_id,
            )
        if not row:
            return None
        return UserProfile(
            user_id=row["user_id"],
            full_name=row["full_name"],
            phone=row["phone"],
            terms_accepted_at=row["terms_accepted_at"],
            language_code=row["language_code"],
        )


def create_user_storage(database_path: Path, timezone: str = "Asia/Tashkent", database_url: str = "") -> UserStorage:
    if database_url:
        return PostgresUserStorage(database_url, timezone)
    return SQLiteUserStorage(database_path, timezone)

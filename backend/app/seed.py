from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin, Answer, Attempt, AttemptResultCache, DailyTestStat, Question, Source, Test, TestAttemptStat, TestRule
from .security import hash_password

settings = get_settings()
TASHKENT = ZoneInfo("Asia/Tashkent")
logger = logging.getLogger(__name__)


def _ensure_search_index() -> None:
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_source_questions_text_trgm "
                    "ON source_questions USING GIN (question_text gin_trgm_ops)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_source_answers_text_trgm "
                    "ON source_answers USING GIN (answer_text gin_trgm_ops)"
                )
            )
    except SQLAlchemyError:
        logger.warning("pg_trgm qidiruv indeksi yaratilmadi; oddiy qidiruv ishlashda davom etadi", exc_info=True)


def _backfill_daily_stats() -> None:
    with SessionLocal() as db:
        if (db.scalar(select(func.count(DailyTestStat.id))) or 0) > 0:
            return
        grouped: dict[tuple[object, str], DailyTestStat] = {}
        for stat in db.scalars(select(TestAttemptStat).order_by(TestAttemptStat.finished_at)):
            finished_at = stat.finished_at if stat.finished_at.tzinfo else stat.finished_at.replace(tzinfo=timezone.utc)
            stat_date = finished_at.astimezone(TASHKENT).date()
            test_key = f"id:{stat.test_id}" if stat.test_id else f"name:{stat.test_name_snapshot.casefold()}"
            key = (stat_date, test_key)
            daily = grouped.get(key)
            if not daily:
                daily = DailyTestStat(
                    stat_date=stat_date,
                    test_key=test_key,
                    test_id=stat.test_id,
                    test_name_snapshot=stat.test_name_snapshot,
                    attempt_count=0,
                    total_questions=0,
                    total_correct=0,
                    total_percentage=0,
                    total_spent_seconds=0,
                )
                grouped[key] = daily
            daily.attempt_count += 1
            daily.total_questions += stat.total_questions
            daily.total_correct += stat.correct_count
            daily.total_percentage += stat.percentage
            daily.total_spent_seconds += stat.spent_seconds
        if grouped:
            db.add_all(grouped.values())
            db.commit()


def _cleanup_transient_data() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(delete(AttemptResultCache).where(AttemptResultCache.expires_at < now))
        db.execute(delete(Attempt).where(Attempt.started_at < now - timedelta(hours=24)))
        old_stat_ids = select(TestAttemptStat.id).order_by(TestAttemptStat.finished_at.desc()).offset(100)
        db.execute(delete(TestAttemptStat).where(TestAttemptStat.id.in_(old_stat_ids)))
        db.commit()


def _run_lightweight_migrations() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "users" in table_names:
        with engine.begin() as connection:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_registered_at ON users (registered_at)"))

    if "error_reports" in table_names:
        existing = {column["name"] for column in inspector.get_columns("error_reports")}
        json_type = "JSON" if engine.dialect.name != "sqlite" else "TEXT"
        additions = {
            "question_id": "INTEGER",
            "attempt_id": "INTEGER",
            "question_text_snapshot": "TEXT",
            "source_name_snapshot": "VARCHAR(255)",
            "answers_snapshot": json_type,
        }
        with engine.begin() as connection:
            for column_name, column_type in additions.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE error_reports ADD COLUMN {column_name} {column_type}"))

    if "source_questions" in table_names:
        existing_questions = {column["name"] for column in inspector.get_columns("source_questions")}
        question_additions = {
            "topic": "VARCHAR(255)",
            "difficulty": "VARCHAR(20) NOT NULL DEFAULT 'medium'",
            "explanation": "TEXT",
        }
        with engine.begin() as connection:
            for column_name, column_type in question_additions.items():
                if column_name not in existing_questions:
                    connection.execute(text(f"ALTER TABLE source_questions ADD COLUMN {column_name} {column_type}"))

    if "tests" in table_names:
        existing_tests = {column["name"] for column in inspector.get_columns("tests")}
        test_additions = {
            "test_mode": "VARCHAR(30) NOT NULL DEFAULT 'exam'",
            "group_question_seconds": "INTEGER NOT NULL DEFAULT 30",
            "group_start_vote_count": "INTEGER NOT NULL DEFAULT 10",
            "group_start_vote_seconds": "INTEGER NOT NULL DEFAULT 120",
            "group_stop_vote_count": "INTEGER NOT NULL DEFAULT 10",
            "group_stop_vote_seconds": "INTEGER NOT NULL DEFAULT 60",
        }
        with engine.begin() as connection:
            for column_name, column_type in test_additions.items():
                if column_name not in existing_tests:
                    connection.execute(text(f"ALTER TABLE tests ADD COLUMN {column_name} {column_type}"))

    if "group_quiz_sessions" in table_names:
        existing_sessions = {column["name"] for column in inspector.get_columns("group_quiz_sessions")}
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
        session_additions = {
            "start_vote_required": "INTEGER NOT NULL DEFAULT 10",
            "start_vote_seconds": "INTEGER NOT NULL DEFAULT 120",
            "start_vote_deadline": timestamp_type,
            "start_vote_message_id": "BIGINT",
            "stop_vote_required": "INTEGER NOT NULL DEFAULT 10",
            "stop_vote_seconds": "INTEGER NOT NULL DEFAULT 60",
            "stop_vote_deadline": timestamp_type,
            "stop_vote_message_id": "BIGINT",
        }
        with engine.begin() as connection:
            for column_name, column_type in session_additions.items():
                if column_name not in existing_sessions:
                    connection.execute(text(f"ALTER TABLE group_quiz_sessions ADD COLUMN {column_name} {column_type}"))

            # Old completed process rows are not needed for statistics or future quizzes.
            connection.execute(text("DELETE FROM group_quiz_sessions WHERE status IN ('finished', 'cancelled')"))

    if "attempts" in table_names:
        existing_attempts = {column["name"] for column in inspector.get_columns("attempts")}
        if "feedback_mode" not in existing_attempts:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE attempts ADD COLUMN feedback_mode VARCHAR(20) NOT NULL DEFAULT 'practice'"))

    if "attempt_questions" in table_names:
        existing_attempt_questions = {column["name"] for column in inspector.get_columns("attempt_questions")}
        if "explanation_snapshot" not in existing_attempt_questions:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE attempt_questions ADD COLUMN explanation_snapshot TEXT"))

    if {"attempts", "attempt_questions", "tests", "test_attempt_stats"}.issubset(table_names):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO test_attempt_stats
                        (test_id, test_name_snapshot, finished_at, total_questions, correct_count, percentage, spent_seconds)
                    SELECT
                        attempts.test_id,
                        tests.name,
                        attempts.finished_at,
                        attempts.total_questions,
                        attempts.correct_count,
                        CASE
                            WHEN attempts.total_questions > 0
                            THEN CAST(ROUND(attempts.correct_count * 100.0 / attempts.total_questions) AS INTEGER)
                            ELSE 0
                        END,
                        0
                    FROM attempts
                    JOIN tests ON tests.id = attempts.test_id
                    WHERE attempts.finished_at IS NOT NULL
                    """
                )
            )
            connection.execute(text("DELETE FROM attempt_questions WHERE attempt_id IN (SELECT id FROM attempts WHERE finished_at IS NOT NULL)"))
            connection.execute(text("DELETE FROM attempts WHERE finished_at IS NOT NULL"))


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()
    _ensure_search_index()
    _backfill_daily_stats()
    _cleanup_transient_data()
    with SessionLocal() as db:
        admin = db.scalar(select(Admin).where(Admin.username == settings.bootstrap_admin_username))
        if not admin and settings.bootstrap_admin_username and settings.bootstrap_admin_password:
            db.add(
                Admin(
                    username=settings.bootstrap_admin_username.strip(),
                    password_hash=hash_password(settings.bootstrap_admin_password),
                )
            )
            db.commit()


def seed_demo_data() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Source.id).limit(1)):
            return
        source = Source(name="Namuna savollar")
        db.add(source)
        db.flush()
        samples = [
            ("O'zbekiston Respublikasining poytaxti qaysi shahar?", [("Toshkent", True), ("Samarqand", False), ("Buxoro", False), ("Nukus", False)]),
            ("2 + 2 nechaga teng?", [("3", False), ("4", True), ("5", False), ("6", False)]),
            ("HTTPS nimani ta'minlaydi?", [("Shifrlangan aloqa", True), ("Faqat rasm yuklash", False), ("Kompyuterni o'chirish", False), ("Domen sotib olish", False)]),
        ]
        for text, answers in samples:
            question = Question(source_id=source.id, question_text=text)
            db.add(question)
            db.flush()
            for position, (answer_text, correct) in enumerate(answers):
                db.add(Answer(question_id=question.id, answer_text=answer_text, is_correct=correct, position=position))
        test = Test(name="Namuna testi", time_limit_minutes=5, is_active=True)
        db.add(test)
        db.flush()
        db.add(TestRule(test_id=test.id, source_id=source.id, question_count=3))
        db.commit()

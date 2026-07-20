from __future__ import annotations

from sqlalchemy import inspect, select, text

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin, Answer, Question, Source, Test, TestRule
from .security import hash_password

settings = get_settings()


def _run_lightweight_migrations() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

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

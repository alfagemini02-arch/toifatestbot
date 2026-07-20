from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    attempts: Mapped[list[Attempt]] = relationship(back_populates="user")
    reports: Mapped[list[ErrorReport]] = relationship(back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    questions: Mapped[list[Question]] = relationship(back_populates="source", cascade="all, delete-orphan")
    rules: Mapped[list[TestRule]] = relationship(back_populates="source")


class Question(Base):
    __tablename__ = "source_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(40), default="multiple_choice", nullable=False)
    topic: Mapped[str | None] = mapped_column(String(255), index=True)
    difficulty: Mapped[str] = mapped_column(String(20), default="medium", index=True, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    source: Mapped[Source] = relationship(back_populates="questions")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="Answer.position",
    )

    __table_args__ = (Index("idx_question_source_text", "source_id", "question_text"),)


class Answer(Base):
    __tablename__ = "source_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("source_questions.id", ondelete="CASCADE"), index=True, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    question: Mapped[Question] = relationship(back_populates="answers")


class Test(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    rules: Mapped[list[TestRule]] = relationship(back_populates="test", cascade="all, delete-orphan")
    attempts: Mapped[list[Attempt]] = relationship(back_populates="test")


class TestRule(Base):
    __tablename__ = "test_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), index=True, nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)

    test: Mapped[Test] = relationship(back_populates="rules")
    source: Mapped[Source] = relationship(back_populates="rules")

    __table_args__ = (UniqueConstraint("test_id", "source_id", name="uq_test_rule_source"),)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="RESTRICT"), index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    user: Mapped[User] = relationship(back_populates="attempts")
    test: Mapped[Test] = relationship(back_populates="attempts")
    questions: Mapped[list[AttemptQuestion]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="AttemptQuestion.order_index",
    )


class AttemptQuestion(Base):
    __tablename__ = "attempt_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True, nullable=False)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("source_questions.id", ondelete="SET NULL"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    answers_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    selected_answer_id: Mapped[int | None] = mapped_column(Integer)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)

    attempt: Mapped[Attempt] = relationship(back_populates="questions")

    __table_args__ = (UniqueConstraint("attempt_id", "order_index", name="uq_attempt_order"),)


class TestAttemptStat(Base):
    __tablename__ = "test_attempt_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id", ondelete="SET NULL"), index=True)
    test_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    percentage: Mapped[int] = mapped_column(Integer, nullable=False)
    spent_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ErrorReport(Base):
    __tablename__ = "error_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("source_questions.id", ondelete="SET NULL"), index=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id", ondelete="SET NULL"), index=True)
    message_text: Mapped[str | None] = mapped_column(Text)
    question_text_snapshot: Mapped[str | None] = mapped_column(Text)
    source_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    answers_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    telegram_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_msg_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    fixed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fixed_by: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[User] = relationship(back_populates="reports")


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_tg_id: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(40))
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import Attempt, AttemptQuestion, ErrorReport, Question, Test, User

TASHKENT = ZoneInfo("Asia/Tashkent")


def as_utc(value: datetime) -> datetime:
    """SQLite timezone ma'lumotini olib tashlasa ham datetime'ni UTC sifatida ishlatadi."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def serialize_question(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "source_id": question.source_id,
        "source_name": question.source.name if question.source else None,
        "question_text": question.question_text,
        "answers": [
            {"id": answer.id, "text": answer.answer_text, "correct": answer.is_correct, "position": answer.position}
            for answer in question.answers
        ],
    }


def test_total_questions(test: Test) -> int:
    return sum(rule.question_count for rule in test.rules)


def serialize_test(test: Test, include_rules: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": test.id,
        "name": test.name,
        "time_limit_minutes": test.time_limit_minutes,
        "is_active": test.is_active,
        "total_questions": test_total_questions(test),
    }
    if include_rules:
        result["rules"] = [
            {
                "id": rule.id,
                "source_id": rule.source_id,
                "source_name": rule.source.name if rule.source else None,
                "question_count": rule.question_count,
                "available_questions": len(rule.source.questions) if rule.source else 0,
            }
            for rule in test.rules
        ]
    return result


def create_attempt(db: Session, user: User, test: Test) -> Attempt:
    existing = db.scalar(
        select(Attempt)
        .where(Attempt.user_id == user.id, Attempt.finished_at.is_(None))
        .order_by(Attempt.started_at.desc())
    )
    if existing:
        raise HTTPException(status_code=409, detail={"message": "Tugallanmagan urinish mavjud", "attempt_id": existing.id})

    selected_questions: list[Question] = []
    for rule in test.rules:
        questions = list(
            db.scalars(
                select(Question)
                .options(selectinload(Question.answers))
                .where(Question.source_id == rule.source_id)
            ).unique()
        )
        if not questions:
            continue
        selected_questions.extend(random.sample(questions, min(rule.question_count, len(questions))))

    if not selected_questions:
        raise HTTPException(status_code=422, detail="Test manbalarida savollar mavjud emas")

    random.shuffle(selected_questions)
    attempt = Attempt(user_id=user.id, test_id=test.id, total_questions=len(selected_questions), correct_count=0)
    db.add(attempt)
    db.flush()

    for index, question in enumerate(selected_questions, start=1):
        answer_snapshot = [
            {"id": answer.id, "text": answer.answer_text, "correct": answer.is_correct}
            for answer in question.answers
        ]
        random.shuffle(answer_snapshot)
        db.add(
            AttemptQuestion(
                attempt_id=attempt.id,
                question_id=question.id,
                order_index=index,
                question_text_snapshot=question.question_text,
                answers_snapshot=answer_snapshot,
            )
        )
    db.commit()
    return get_attempt(db, attempt.id, user.id)


def get_attempt(db: Session, attempt_id: int, user_id: int) -> Attempt:
    attempt = db.scalar(
        select(Attempt)
        .options(selectinload(Attempt.questions), selectinload(Attempt.test))
        .where(Attempt.id == attempt_id, Attempt.user_id == user_id)
    )
    if not attempt:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    return attempt


def remaining_seconds(attempt: Attempt) -> int | None:
    if not attempt.test.time_limit_minutes:
        return None
    deadline = as_utc(attempt.started_at) + timedelta(minutes=attempt.test.time_limit_minutes)
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


def auto_finish_if_expired(db: Session, attempt: Attempt) -> bool:
    remaining = remaining_seconds(attempt)
    if attempt.finished_at is None and remaining is not None and remaining <= 0:
        finish_attempt(db, attempt)
        return True
    return False


def serialize_attempt(attempt: Attempt, include_correct_for_answered: bool = True) -> dict[str, Any]:
    questions: list[dict[str, Any]] = []
    for item in sorted(attempt.questions, key=lambda row: row.order_index):
        answers: list[dict[str, Any]] = []
        for answer in item.answers_snapshot:
            payload = {"id": answer["id"], "text": answer["text"]}
            if include_correct_for_answered and item.selected_answer_id is not None:
                payload["correct"] = bool(answer.get("correct"))
            answers.append(payload)
        questions.append(
            {
                "id": item.id,
                "question_id": item.question_id or item.id,
                "order_index": item.order_index,
                "question_text": item.question_text_snapshot,
                "answers": answers,
                "selected_answer_id": item.selected_answer_id,
                "is_correct": item.is_correct,
            }
        )
    return {
        "id": attempt.id,
        "test_id": attempt.test_id,
        "test_name": attempt.test.name,
        "started_at": attempt.started_at.isoformat(),
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "total_questions": attempt.total_questions,
        "correct_count": attempt.correct_count,
        "time_limit_minutes": attempt.test.time_limit_minutes,
        "remaining_seconds": remaining_seconds(attempt),
        "questions": questions,
    }


def submit_answer(db: Session, attempt: Attempt, question_id: int, answer_id: int) -> dict[str, Any]:
    if attempt.finished_at:
        raise HTTPException(status_code=409, detail="Test allaqachon yakunlangan")
    if auto_finish_if_expired(db, attempt):
        raise HTTPException(status_code=409, detail="Test vaqti tugagan")

    item = next((row for row in attempt.questions if (row.question_id or row.id) == question_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Savol ushbu urinishga tegishli emas")
    if item.selected_answer_id is not None:
        raise HTTPException(status_code=409, detail="Bu savolga avval javob berilgan")

    answer = next((value for value in item.answers_snapshot if int(value["id"]) == answer_id), None)
    if not answer:
        raise HTTPException(status_code=422, detail="Javob varianti ushbu savolga tegishli emas")

    correct_answer = next(value for value in item.answers_snapshot if value.get("correct"))
    item.selected_answer_id = answer_id
    item.is_correct = bool(answer.get("correct"))
    if item.is_correct:
        attempt.correct_count += 1
    db.commit()
    return {
        "is_correct": item.is_correct,
        "correct_answer_id": int(correct_answer["id"]),
        "selected_answer_id": answer_id,
    }


def finish_attempt(db: Session, attempt: Attempt) -> dict[str, Any]:
    if not attempt.finished_at:
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.correct_count = sum(1 for item in attempt.questions if item.is_correct)
        db.commit()
    answered = sum(1 for item in attempt.questions if item.selected_answer_id is not None)
    total = attempt.total_questions
    correct = attempt.correct_count
    percentage = round((correct / total) * 100) if total else 0
    finished_at = as_utc(attempt.finished_at) if attempt.finished_at else datetime.now(timezone.utc)
    spent_seconds = int((finished_at - as_utc(attempt.started_at)).total_seconds())
    return {
        "attempt_id": attempt.id,
        "test_name": attempt.test.name,
        "total": total,
        "answered": answered,
        "correct": correct,
        "incorrect": answered - correct,
        "unanswered": total - answered,
        "percentage": percentage,
        "spent_seconds": max(0, spent_seconds),
    }


def review_attempt(attempt: Attempt) -> dict[str, Any]:
    if not attempt.finished_at:
        raise HTTPException(status_code=409, detail="Test hali yakunlanmagan")
    rows = []
    for item in sorted(attempt.questions, key=lambda row: row.order_index):
        correct = next((value for value in item.answers_snapshot if value.get("correct")), None)
        selected = next((value for value in item.answers_snapshot if value["id"] == item.selected_answer_id), None)
        rows.append(
            {
                "order_index": item.order_index,
                "question_text": item.question_text_snapshot,
                "answers": [{"id": value["id"], "text": value["text"], "correct": bool(value.get("correct"))} for value in item.answers_snapshot],
                "selected_answer_id": item.selected_answer_id,
                "selected_answer_text": selected["text"] if selected else None,
                "correct_answer_id": correct["id"] if correct else None,
                "correct_answer_text": correct["text"] if correct else None,
                "is_correct": item.is_correct,
            }
        )
    return {"attempt_id": attempt.id, "questions": rows}


def user_stats(db: Session, user: User) -> dict[str, Any]:
    finished = list(
        db.scalars(
            select(Attempt)
            .options(selectinload(Attempt.test))
            .where(Attempt.user_id == user.id, Attempt.finished_at.is_not(None))
        )
    )
    percentages = [round(item.correct_count * 100 / item.total_questions) for item in finished if item.total_questions]
    best = max(finished, key=lambda item: item.correct_count / item.total_questions if item.total_questions else 0, default=None)
    now_tashkent = datetime.now(TASHKENT)
    today_count = sum(
        1
        for item in finished
        if item.finished_at and as_utc(item.finished_at).astimezone(TASHKENT).date() == now_tashkent.date()
    )
    return {
        "count": len(finished),
        "average": round(sum(percentages) / len(percentages)) if percentages else 0,
        "best_percentage": round(best.correct_count * 100 / best.total_questions) if best and best.total_questions else 0,
        "best_test": best.test.name if best else None,
        "today": today_count,
    }


def admin_dashboard_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(TASHKENT)
    today_start_local = datetime(now.year, now.month, now.day, tzinfo=TASHKENT)
    today_start = today_start_local.astimezone(timezone.utc)
    week_start = (today_start_local - timedelta(days=today_start_local.weekday())).astimezone(timezone.utc)

    total_users = db.scalar(select(func.count(User.id))) or 0
    today_users = db.scalar(select(func.count(User.id)).where(User.registered_at >= today_start)) or 0
    week_users = db.scalar(select(func.count(User.id)).where(User.registered_at >= week_start)) or 0
    total_attempts = db.scalar(select(func.count(Attempt.id)).where(Attempt.finished_at.is_not(None))) or 0
    today_attempts = db.scalar(select(func.count(Attempt.id)).where(Attempt.finished_at >= today_start)) or 0
    open_reports = db.scalar(select(func.count(ErrorReport.id)).where(ErrorReport.status == "open")) or 0
    fixed_reports = db.scalar(select(func.count(ErrorReport.id)).where(ErrorReport.status == "fixed")) or 0

    average = db.scalar(
        select(func.avg(Attempt.correct_count * 100.0 / func.nullif(Attempt.total_questions, 0))).where(Attempt.finished_at.is_not(None))
    ) or 0

    popular = db.execute(
        select(Test.name, func.count(Attempt.id).label("count"))
        .join(Attempt, Attempt.test_id == Test.id)
        .where(Attempt.finished_at.is_not(None))
        .group_by(Test.id)
        .order_by(func.count(Attempt.id).desc())
        .limit(1)
    ).first()

    last_7: list[dict[str, Any]] = []
    for offset in range(6, -1, -1):
        day_local = today_start_local - timedelta(days=offset)
        next_local = day_local + timedelta(days=1)
        count = db.scalar(
            select(func.count(Attempt.id)).where(
                Attempt.finished_at >= day_local.astimezone(timezone.utc),
                Attempt.finished_at < next_local.astimezone(timezone.utc),
            )
        ) or 0
        last_7.append({"date": day_local.strftime("%d.%m"), "count": count})

    recent_rows = db.execute(
        select(Attempt, User, Test)
        .join(User, User.id == Attempt.user_id)
        .join(Test, Test.id == Attempt.test_id)
        .where(Attempt.finished_at.is_not(None))
        .order_by(Attempt.finished_at.desc())
        .limit(10)
    ).all()
    recent = [
        {
            "id": attempt.id,
            "user": user.full_name,
            "test": test.name,
            "percentage": round(attempt.correct_count * 100 / attempt.total_questions) if attempt.total_questions else 0,
            "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        }
        for attempt, user, test in recent_rows
    ]

    return {
        "users": {"today": today_users, "week": week_users, "total": total_users},
        "attempts": {"today": today_attempts, "total": total_attempts, "average": round(float(average))},
        "reports": {"open": open_reports, "fixed": fixed_reports},
        "popular_test": {"name": popular[0], "count": popular[1]} if popular else None,
        "last_7_days": last_7,
        "recent_attempts": recent,
        "updated_at": datetime.now(TASHKENT).isoformat(),
    }

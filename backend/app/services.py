from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .models import Answer, Attempt, AttemptQuestion, ErrorReport, Question, QuestionClassificationVote, Source, Test, TestAttemptStat, User

TASHKENT = ZoneInfo("Asia/Tashkent")
CLASSIFIER_MODE = "classifier"
EXAM_MODE = "exam"
REAL_APPEARED_SOURCE_NAME = "Haqiqiy tushgan"
APPEARED_THRESHOLD = 3
CLASSIFIER_APPEARED_ID = 1
CLASSIFIER_NOT_APPEARED_ID = 0


def as_utc(value: datetime) -> datetime:
    """SQLite timezone ma'lumotini olib tashlasa ham datetime'ni UTC sifatida ishlatadi."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def serialize_question(question: Question) -> dict[str, Any]:
    return {
        "id": question.id,
        "source_id": question.source_id,
        "source_name": question.source.name if question.source else None,
        "question_text": question.question_text,
        "topic": question.topic,
        "difficulty": question.difficulty,
        "explanation": question.explanation,
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
        "test_mode": test.test_mode,
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
    existing_attempts = list(db.scalars(select(Attempt).where(Attempt.user_id == user.id, Attempt.finished_at.is_(None))))
    for existing in existing_attempts:
        db.delete(existing)
    if existing_attempts:
        db.flush()

    selected_questions: list[Question] = []
    for rule in test.rules:
        if test.test_mode == CLASSIFIER_MODE:
            questions = list_candidate_classification_questions(db, user, rule.source_id)
        else:
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
        detail = "Ajratish sinovi uchun yangi savollar qolmagan" if test.test_mode == CLASSIFIER_MODE else "Test manbalarida savollar mavjud emas"
        raise HTTPException(status_code=422, detail=detail)

    random.shuffle(selected_questions)
    attempt = Attempt(user_id=user.id, test_id=test.id, total_questions=len(selected_questions), correct_count=0)
    db.add(attempt)
    db.flush()

    for index, question in enumerate(selected_questions, start=1):
        explanation = question.explanation
        answer_snapshot = [{"id": answer.id, "text": answer.answer_text, "correct": answer.is_correct, "_explanation": explanation} for answer in question.answers]
        if test.test_mode != CLASSIFIER_MODE:
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
        "test_mode": attempt.test.test_mode,
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
    is_correct = bool(answer.get("correct"))
    correct_answer_id = int(correct_answer["id"])
    explanation = next((str(value.get("_explanation")) for value in item.answers_snapshot if value.get("_explanation")), None)
    item.selected_answer_id = answer_id
    item.is_correct = is_correct
    if is_correct:
        attempt.correct_count += 1
    db.commit()
    return {
        "is_correct": is_correct,
        "correct_answer_id": correct_answer_id,
        "selected_answer_id": answer_id,
        "explanation": explanation,
    }


def submit_answer_by_id(db: Session, attempt_id: int, user_id: int, question_id: int, answer_id: int) -> dict[str, Any]:
    attempt = db.scalar(
        select(Attempt)
        .options(selectinload(Attempt.test))
        .where(Attempt.id == attempt_id, Attempt.user_id == user_id)
    )
    if not attempt:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    if attempt.test.test_mode == CLASSIFIER_MODE:
        raise HTTPException(status_code=409, detail="Bu ajratish sinovi. Tushgan yoki tushmagan deb belgilang")
    if attempt.finished_at:
        raise HTTPException(status_code=409, detail="Test allaqachon yakunlangan")
    if auto_finish_if_expired(db, attempt):
        raise HTTPException(status_code=409, detail="Test vaqti tugagan")

    item = db.scalar(
        select(AttemptQuestion).where(
            AttemptQuestion.attempt_id == attempt.id,
            or_(AttemptQuestion.question_id == question_id, AttemptQuestion.id == question_id),
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Savol ushbu urinishga tegishli emas")
    if item.selected_answer_id is not None:
        raise HTTPException(status_code=409, detail="Bu savolga avval javob berilgan")

    answer = next((value for value in item.answers_snapshot if int(value["id"]) == answer_id), None)
    if not answer:
        raise HTTPException(status_code=422, detail="Javob varianti ushbu savolga tegishli emas")

    correct_answer = next(value for value in item.answers_snapshot if value.get("correct"))
    is_correct = bool(answer.get("correct"))
    correct_answer_id = int(correct_answer["id"])
    explanation = next((str(value.get("_explanation")) for value in item.answers_snapshot if value.get("_explanation")), None)
    item.selected_answer_id = answer_id
    item.is_correct = is_correct
    if is_correct:
        attempt.correct_count += 1
    db.commit()
    return {
        "is_correct": is_correct,
        "correct_answer_id": correct_answer_id,
        "selected_answer_id": answer_id,
        "explanation": explanation,
    }


def list_candidate_classification_questions(db: Session, user: User, source_id: int) -> list[Question]:
    voted_subquery = select(QuestionClassificationVote.question_id).where(QuestionClassificationVote.user_id == user.id)
    promoted_subquery = (
        select(QuestionClassificationVote.question_id)
        .where(QuestionClassificationVote.vote == "appeared")
        .group_by(QuestionClassificationVote.question_id)
        .having(func.count(QuestionClassificationVote.id) >= APPEARED_THRESHOLD)
    )
    real_source_texts = (
        select(Question.question_text)
        .join(Source, Source.id == Question.source_id)
        .where(Source.name == REAL_APPEARED_SOURCE_NAME)
    )
    return list(
        db.scalars(
            select(Question)
            .options(selectinload(Question.answers), selectinload(Question.source))
            .where(
                Question.source_id == source_id,
                Question.id.not_in(voted_subquery),
                Question.id.not_in(promoted_subquery),
                Question.question_text.not_in(real_source_texts),
            )
        ).unique()
    )


def ensure_real_appeared_source(db: Session) -> Source:
    source = db.scalar(select(Source).where(Source.name == REAL_APPEARED_SOURCE_NAME))
    if source:
        return source
    source = Source(name=REAL_APPEARED_SOURCE_NAME)
    db.add(source)
    db.flush()
    return source


def promote_question_if_needed(db: Session, question: Question) -> bool:
    appeared_count = db.scalar(
        select(func.count(QuestionClassificationVote.id)).where(
            QuestionClassificationVote.question_id == question.id,
            QuestionClassificationVote.vote == "appeared",
        )
    ) or 0
    if appeared_count < APPEARED_THRESHOLD:
        return False
    target_source = ensure_real_appeared_source(db)
    existing = db.scalar(select(Question).where(Question.source_id == target_source.id, Question.question_text == question.question_text))
    if existing:
        return False
    clone = Question(
        source_id=target_source.id,
        question_text=question.question_text,
        question_type=question.question_type,
        topic=question.topic,
        difficulty=question.difficulty,
        explanation=question.explanation,
    )
    db.add(clone)
    db.flush()
    for position, answer in enumerate(question.answers):
        db.add(Answer(question_id=clone.id, answer_text=answer.answer_text, is_correct=answer.is_correct, position=position))
    return True


def submit_classification_by_id(db: Session, attempt_id: int, user: User, question_id: int, appeared: bool) -> dict[str, Any]:
    attempt = db.scalar(
        select(Attempt)
        .options(selectinload(Attempt.test))
        .where(Attempt.id == attempt_id, Attempt.user_id == user.id)
    )
    if not attempt:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    if attempt.test.test_mode != CLASSIFIER_MODE:
        raise HTTPException(status_code=409, detail="Bu oddiy test. Javob variantini tanlang")
    if attempt.finished_at:
        raise HTTPException(status_code=409, detail="Test allaqachon yakunlangan")
    if auto_finish_if_expired(db, attempt):
        raise HTTPException(status_code=409, detail="Test vaqti tugagan")

    item = db.scalar(
        select(AttemptQuestion).where(
            AttemptQuestion.attempt_id == attempt.id,
            or_(AttemptQuestion.question_id == question_id, AttemptQuestion.id == question_id),
        )
    )
    if not item or not item.question_id:
        raise HTTPException(status_code=404, detail="Savol ushbu urinishga tegishli emas")
    if item.selected_answer_id is not None:
        raise HTTPException(status_code=409, detail="Bu savol avval belgilangan")

    question = db.scalar(
        select(Question)
        .options(selectinload(Question.answers), selectinload(Question.source))
        .where(Question.id == item.question_id)
    )
    if not question:
        raise HTTPException(status_code=404, detail="Savol topilmadi")

    vote = QuestionClassificationVote(
        user_id=user.id,
        question_id=question.id,
        test_id=attempt.test_id,
        vote="appeared" if appeared else "not_appeared",
    )
    db.add(vote)
    item.selected_answer_id = CLASSIFIER_APPEARED_ID if appeared else CLASSIFIER_NOT_APPEARED_ID
    item.is_correct = appeared
    if appeared:
        attempt.correct_count += 1
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bu savolni avval belgilagansiz") from exc

    promoted = promote_question_if_needed(db, question) if appeared else False
    db.commit()
    appeared_count = db.scalar(
        select(func.count(QuestionClassificationVote.id)).where(
            QuestionClassificationVote.question_id == question.id,
            QuestionClassificationVote.vote == "appeared",
        )
    ) or 0
    return {
        "selected_answer_id": item.selected_answer_id,
        "appeared": appeared,
        "appeared_count": appeared_count,
        "promoted": promoted,
    }


def finish_attempt(db: Session, attempt: Attempt) -> dict[str, Any]:
    if not attempt.finished_at:
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.correct_count = sum(1 for item in attempt.questions if item.is_correct)
    answered = sum(1 for item in attempt.questions if item.selected_answer_id is not None)
    total = attempt.total_questions
    correct = attempt.correct_count
    percentage = round((correct / total) * 100) if total else 0
    if attempt.test.test_mode == CLASSIFIER_MODE:
        percentage = round((answered / total) * 100) if total else 0
    finished_at = as_utc(attempt.finished_at) if attempt.finished_at else datetime.now(timezone.utc)
    spent_seconds = int((finished_at - as_utc(attempt.started_at)).total_seconds())
    question_ids = [item.question_id for item in attempt.questions if item.question_id]
    source_questions = {
        question.id: question
        for question in db.scalars(select(Question).where(Question.id.in_(question_ids)))
    } if question_ids else {}
    topic_map: dict[str, dict[str, int]] = {}
    for item in attempt.questions:
        question = source_questions.get(item.question_id or 0)
        topic = (question.topic if question and question.topic else "Umumiy").strip() or "Umumiy"
        row = topic_map.setdefault(topic, {"total": 0, "correct": 0})
        row["total"] += 1
        if item.is_correct:
            row["correct"] += 1
    topic_stats = [
        {"topic": topic, "total": values["total"], "correct": values["correct"], "percentage": round(values["correct"] * 100 / values["total"]) if values["total"] else 0}
        for topic, values in sorted(topic_map.items())
    ]
    result = {
        "attempt_id": attempt.id,
        "test_name": attempt.test.name,
        "test_mode": attempt.test.test_mode,
        "total": total,
        "answered": answered,
        "correct": correct,
        "incorrect": answered - correct,
        "unanswered": total - answered,
        "percentage": percentage,
        "spent_seconds": max(0, spent_seconds),
        "topic_stats": topic_stats,
    }
    db.add(
        TestAttemptStat(
            test_id=attempt.test_id,
            test_name_snapshot=attempt.test.name,
            finished_at=finished_at,
            total_questions=total,
            correct_count=correct,
            percentage=percentage,
            spent_seconds=max(0, spent_seconds),
        )
    )
    db.delete(attempt)
    db.commit()
    return result


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
    return {
        "count": 0,
        "average": 0,
        "best_percentage": 0,
        "best_test": None,
        "today": 0,
    }


def admin_dashboard_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(TASHKENT)
    today_start_local = datetime(now.year, now.month, now.day, tzinfo=TASHKENT)
    today_start = today_start_local.astimezone(timezone.utc)
    week_start = (today_start_local - timedelta(days=today_start_local.weekday())).astimezone(timezone.utc)

    total_users = db.scalar(select(func.count(User.id))) or 0
    today_users = db.scalar(select(func.count(User.id)).where(User.registered_at >= today_start)) or 0
    week_users = db.scalar(select(func.count(User.id)).where(User.registered_at >= week_start)) or 0
    total_attempts = db.scalar(select(func.count(TestAttemptStat.id))) or 0
    today_attempts = db.scalar(select(func.count(TestAttemptStat.id)).where(TestAttemptStat.finished_at >= today_start)) or 0
    open_reports = db.scalar(select(func.count(ErrorReport.id)).where(ErrorReport.status == "open")) or 0
    fixed_reports = db.scalar(select(func.count(ErrorReport.id)).where(ErrorReport.status == "fixed")) or 0

    average = db.scalar(
        select(func.avg(TestAttemptStat.percentage))
    ) or 0

    popular = db.execute(
        select(TestAttemptStat.test_name_snapshot, func.count(TestAttemptStat.id).label("count"))
        .group_by(TestAttemptStat.test_name_snapshot)
        .order_by(func.count(TestAttemptStat.id).desc())
        .limit(1)
    ).first()

    last_7: list[dict[str, Any]] = []
    for offset in range(6, -1, -1):
        day_local = today_start_local - timedelta(days=offset)
        next_local = day_local + timedelta(days=1)
        count = db.scalar(
            select(func.count(TestAttemptStat.id)).where(
                TestAttemptStat.finished_at >= day_local.astimezone(timezone.utc),
                TestAttemptStat.finished_at < next_local.astimezone(timezone.utc),
            )
        ) or 0
        last_7.append({"date": day_local.strftime("%d.%m"), "count": count})

    recent_rows = list(
        db.scalars(select(TestAttemptStat).order_by(TestAttemptStat.finished_at.desc()).limit(10))
    )
    recent = [
        {
            "id": stat.id,
            "user": "Saqlanmagan",
            "test": stat.test_name_snapshot,
            "percentage": stat.percentage,
            "finished_at": stat.finished_at.isoformat(),
        }
        for stat in recent_rows
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

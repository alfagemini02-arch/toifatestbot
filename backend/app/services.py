from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .models import Answer, Attempt, AttemptQuestion, AttemptResultCache, DailyTestStat, ErrorReport, Question, QuestionClassificationVote, Source, Test, TestAttemptStat, User

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


def serialize_test(test: Test, include_rules: bool = True, available_counts: dict[int, int] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": test.id,
        "name": test.name,
        "test_mode": test.test_mode,
        "time_limit_minutes": test.time_limit_minutes,
        "group_question_seconds": test.group_question_seconds,
        "group_start_vote_count": test.group_start_vote_count,
        "group_start_vote_seconds": test.group_start_vote_seconds,
        "group_stop_vote_count": test.group_stop_vote_count,
        "group_stop_vote_seconds": test.group_stop_vote_seconds,
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
                "available_questions": (
                    available_counts.get(rule.source_id, 0)
                    if available_counts is not None
                    else len(rule.source.questions) if rule.source else 0
                ),
            }
            for rule in test.rules
        ]
    return result


def _rule_allocations(test: Test, requested_count: int | None) -> list[int]:
    quotas = [rule.question_count for rule in test.rules]
    full_total = sum(quotas)
    target = min(requested_count or full_total, full_total)
    if target >= full_total:
        return quotas
    raw = [quota * target / full_total for quota in quotas]
    allocations = [min(quota, int(value)) for quota, value in zip(quotas, raw, strict=True)]
    remaining = target - sum(allocations)
    order = sorted(range(len(quotas)), key=lambda index: raw[index] - allocations[index], reverse=True)
    for index in order:
        if remaining <= 0:
            break
        if allocations[index] < quotas[index]:
            allocations[index] += 1
            remaining -= 1
    return allocations


def create_attempt(
    db: Session,
    user: User,
    test: Test,
    requested_count: int | None = None,
    feedback_mode: str = "practice",
) -> Attempt:
    db.execute(delete(AttemptResultCache).where(AttemptResultCache.expires_at < datetime.now(timezone.utc)))
    existing_attempts = list(db.scalars(select(Attempt).where(Attempt.user_id == user.id, Attempt.finished_at.is_(None))))
    for existing in existing_attempts:
        db.delete(existing)
    if existing_attempts:
        db.flush()

    selected_questions: list[Question] = []
    for rule, allocation in zip(test.rules, _rule_allocations(test, requested_count), strict=True):
        if allocation <= 0:
            continue
        if test.test_mode == CLASSIFIER_MODE:
            questions = list_candidate_classification_questions(db, user, rule.source_id, allocation)
        else:
            question_ids = list(
                db.scalars(
                    select(Question.id)
                    .where(Question.source_id == rule.source_id)
                    .order_by(func.random())
                    .limit(allocation)
                )
            )
            questions = list(
                db.scalars(
                    select(Question)
                    .options(selectinload(Question.answers))
                    .where(Question.id.in_(question_ids))
                ).unique()
            ) if question_ids else []
        if not questions:
            continue
        selected_questions.extend(questions)

    if not selected_questions:
        detail = "Ajratish sinovi uchun yangi savollar qolmagan" if test.test_mode == CLASSIFIER_MODE else "Test manbalarida savollar mavjud emas"
        raise HTTPException(status_code=422, detail=detail)

    random.shuffle(selected_questions)
    attempt = Attempt(
        user_id=user.id,
        test_id=test.id,
        total_questions=len(selected_questions),
        correct_count=0,
        feedback_mode=feedback_mode,
    )
    db.add(attempt)
    db.flush()

    for index, question in enumerate(selected_questions, start=1):
        answer_snapshot = [{"id": answer.id, "text": answer.answer_text, "correct": answer.is_correct} for answer in question.answers]
        if test.test_mode != CLASSIFIER_MODE:
            random.shuffle(answer_snapshot)
        db.add(
            AttemptQuestion(
                attempt_id=attempt.id,
                question_id=question.id,
                order_index=index,
                question_text_snapshot=question.question_text,
                explanation_snapshot=question.explanation,
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


def attempt_explanation(item: AttemptQuestion) -> str | None:
    return item.explanation_snapshot or next(
        (str(value.get("_explanation")) for value in item.answers_snapshot if value.get("_explanation")),
        None,
    )


def auto_finish_if_expired(db: Session, attempt: Attempt) -> bool:
    remaining = remaining_seconds(attempt)
    if attempt.finished_at is None and remaining is not None and remaining <= 0:
        finish_attempt(db, attempt)
        return True
    return False


def serialize_attempt(attempt: Attempt, include_correct_for_answered: bool = True) -> dict[str, Any]:
    reveal_answers = include_correct_for_answered and attempt.feedback_mode == "practice"
    questions: list[dict[str, Any]] = []
    for item in sorted(attempt.questions, key=lambda row: row.order_index):
        answers: list[dict[str, Any]] = []
        for answer in item.answers_snapshot:
            payload = {"id": answer["id"], "text": answer["text"]}
            if reveal_answers and item.selected_answer_id is not None:
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
                "explanation": attempt_explanation(item) if reveal_answers and item.selected_answer_id is not None else None,
            }
        )
    return {
        "id": attempt.id,
        "test_id": attempt.test_id,
        "test_name": attempt.test.name,
        "test_mode": attempt.test.test_mode,
        "feedback_mode": attempt.feedback_mode,
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
    explanation = attempt_explanation(item)
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
        ).with_for_update()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Savol ushbu urinishga tegishli emas")
    if item.selected_answer_id is not None:
        stored_correct = next(value for value in item.answers_snapshot if value.get("correct"))
        response = {"accepted": True, "already_answered": True, "selected_answer_id": item.selected_answer_id}
        if attempt.feedback_mode == "practice":
            response.update({
                "is_correct": bool(item.is_correct),
                "correct_answer_id": int(stored_correct["id"]),
                "explanation": attempt_explanation(item),
            })
        return response

    answer = next((value for value in item.answers_snapshot if int(value["id"]) == answer_id), None)
    if not answer:
        raise HTTPException(status_code=422, detail="Javob varianti ushbu savolga tegishli emas")

    correct_answer = next(value for value in item.answers_snapshot if value.get("correct"))
    is_correct = bool(answer.get("correct"))
    correct_answer_id = int(correct_answer["id"])
    item.selected_answer_id = answer_id
    item.is_correct = is_correct
    if is_correct:
        attempt.correct_count += 1
    db.commit()
    response = {"accepted": True, "already_answered": False, "selected_answer_id": answer_id}
    if attempt.feedback_mode == "practice":
        response.update({
            "is_correct": is_correct,
            "correct_answer_id": correct_answer_id,
            "explanation": attempt_explanation(item),
        })
    return response


def list_candidate_classification_questions(db: Session, user: User, source_id: int, limit: int | None = None) -> list[Question]:
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
    statement = (
        select(Question)
        .options(selectinload(Question.answers), selectinload(Question.source))
        .where(
            Question.source_id == source_id,
            Question.id.not_in(voted_subquery),
            Question.id.not_in(promoted_subquery),
            Question.question_text.not_in(real_source_texts),
        )
        .order_by(func.random())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(
        db.scalars(
            statement
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
        ).with_for_update()
    )
    if not item or not item.question_id:
        raise HTTPException(status_code=404, detail="Savol ushbu urinishga tegishli emas")
    if item.selected_answer_id is not None:
        existing_appeared = item.selected_answer_id == CLASSIFIER_APPEARED_ID
        appeared_count = db.scalar(
            select(func.count(QuestionClassificationVote.id)).where(
                QuestionClassificationVote.question_id == item.question_id,
                QuestionClassificationVote.vote == "appeared",
            )
        ) or 0
        return {
            "selected_answer_id": item.selected_answer_id,
            "appeared": existing_appeared,
            "appeared_count": appeared_count,
            "promoted": False,
            "already_answered": True,
        }

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
    appeared_count = db.scalar(
        select(func.count(QuestionClassificationVote.id)).where(
            QuestionClassificationVote.question_id == question.id,
            QuestionClassificationVote.vote == "appeared",
        )
    ) or 0
    if promoted:
        db.execute(delete(QuestionClassificationVote).where(QuestionClassificationVote.question_id == question.id))
    db.commit()
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
    review = []
    for item in sorted(attempt.questions, key=lambda row: row.order_index):
        correct_answer = next((value for value in item.answers_snapshot if value.get("correct")), None)
        review.append(
            {
                "order_index": item.order_index,
                "question_text": item.question_text_snapshot,
                "answers": [
                    {"id": value["id"], "text": value["text"], "correct": bool(value.get("correct"))}
                    for value in item.answers_snapshot
                ],
                "selected_answer_id": item.selected_answer_id,
                "correct_answer_id": correct_answer["id"] if correct_answer else None,
                "is_correct": item.is_correct,
                "explanation": attempt_explanation(item),
            }
        )
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
        "review": review,
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
    local_date = finished_at.astimezone(TASHKENT).date()
    test_key = f"id:{attempt.test_id}" if attempt.test_id else f"name:{attempt.test.name.casefold()}"
    daily = db.scalar(
        select(DailyTestStat)
        .where(DailyTestStat.stat_date == local_date, DailyTestStat.test_key == test_key)
        .with_for_update()
    )
    if not daily:
        daily = DailyTestStat(
            stat_date=local_date,
            test_key=test_key,
            test_id=attempt.test_id,
            test_name_snapshot=attempt.test.name,
            attempt_count=0,
            total_questions=0,
            total_correct=0,
            total_percentage=0,
            total_spent_seconds=0,
        )
        db.add(daily)
    daily.attempt_count += 1
    daily.total_questions += total
    daily.total_correct += correct
    daily.total_percentage += percentage
    daily.total_spent_seconds += max(0, spent_seconds)
    db.add(
        AttemptResultCache(
            attempt_id=attempt.id,
            user_id=attempt.user_id,
            result=result,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    )
    db.delete(attempt)
    db.flush()
    old_stat_ids = select(TestAttemptStat.id).order_by(TestAttemptStat.finished_at.desc()).offset(100)
    db.execute(delete(TestAttemptStat).where(TestAttemptStat.id.in_(old_stat_ids)))
    db.commit()
    return result


def finish_attempt_by_id(db: Session, attempt_id: int, user_id: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cached = db.scalar(
        select(AttemptResultCache).where(
            AttemptResultCache.attempt_id == attempt_id,
            AttemptResultCache.user_id == user_id,
            AttemptResultCache.expires_at >= now,
        )
    )
    if cached:
        return cached.result
    attempt = get_attempt(db, attempt_id, user_id)
    return finish_attempt(db, attempt)


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

    total_users, today_users, week_users = db.execute(
        select(
            func.count(User.id),
            func.count(User.id).filter(User.registered_at >= today_start),
            func.count(User.id).filter(User.registered_at >= week_start),
        )
    ).one()

    day_ranges: list[tuple[object, str]] = []
    for offset in range(6, -1, -1):
        day_local = today_start_local - timedelta(days=offset)
        day_ranges.append((day_local.date(), day_local.strftime("%d.%m")))

    attempt_metrics = db.execute(
        select(
            func.coalesce(func.sum(DailyTestStat.attempt_count), 0),
            func.coalesce(func.sum(DailyTestStat.attempt_count).filter(DailyTestStat.stat_date == today_start_local.date()), 0),
            func.coalesce(func.sum(DailyTestStat.total_percentage), 0),
            *[
                func.coalesce(func.sum(DailyTestStat.attempt_count).filter(DailyTestStat.stat_date == stat_date), 0)
                for stat_date, _ in day_ranges
            ],
        )
    ).one()
    total_attempts = int(attempt_metrics[0] or 0)
    today_attempts = int(attempt_metrics[1] or 0)
    average = float(attempt_metrics[2] or 0) / total_attempts if total_attempts else 0
    last_7 = [
        {"date": label, "count": int(attempt_metrics[index + 3] or 0)}
        for index, (_, label) in enumerate(day_ranges)
    ]

    open_reports, fixed_reports = db.execute(
        select(
            func.count(ErrorReport.id).filter(ErrorReport.status == "open"),
            func.count(ErrorReport.id).filter(ErrorReport.status == "fixed"),
        )
    ).one()

    popular = db.execute(
        select(DailyTestStat.test_name_snapshot, func.sum(DailyTestStat.attempt_count).label("count"))
        .group_by(DailyTestStat.test_name_snapshot)
        .order_by(func.sum(DailyTestStat.attempt_count).desc())
        .limit(1)
    ).first()

    recent_rows = list(
        db.scalars(select(TestAttemptStat).order_by(TestAttemptStat.finished_at.desc()).limit(10))
    )
    recent = [
        {
            "id": stat.id,
            "test": stat.test_name_snapshot,
            "percentage": stat.percentage,
            "correct_count": stat.correct_count,
            "total_questions": stat.total_questions,
            "spent_seconds": stat.spent_seconds,
            "finished_at": stat.finished_at.isoformat(),
        }
        for stat in recent_rows
    ]

    return {
        "users": {"today": int(today_users or 0), "week": int(week_users or 0), "total": int(total_users or 0)},
        "attempts": {"today": today_attempts, "total": total_attempts, "average": round(average)},
        "reports": {"open": int(open_reports or 0), "fixed": int(fixed_reports or 0)},
        "popular_test": {"name": popular[0], "count": popular[1]} if popular else None,
        "last_7_days": last_7,
        "recent_attempts": recent,
        "updated_at": datetime.now(TASHKENT).isoformat(),
    }

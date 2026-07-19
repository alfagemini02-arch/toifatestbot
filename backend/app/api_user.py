from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .database import get_db
from .deps import enforce_rate_limit, get_current_user
from .models import Attempt, Question, Source, Test, TestRule, User
from .schemas import AnswerSubmit, AttemptCreate
from .services import (
    auto_finish_if_expired,
    create_attempt,
    finish_attempt,
    get_attempt,
    review_attempt,
    serialize_attempt,
    serialize_test,
    submit_answer,
    user_stats,
)

router = APIRouter(prefix="/api", tags=["user"], dependencies=[Depends(enforce_rate_limit)])


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "full_name": user.full_name,
        "username": user.username,
        "registered_at": user.registered_at.isoformat(),
        "stats": user_stats(db, user),
    }


@router.get("/tests")
def list_tests(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:  # noqa: ARG001
    tests = list(
        db.scalars(
            select(Test)
            .options(selectinload(Test.rules).selectinload(TestRule.source).selectinload(Source.questions))
            .where(Test.is_active.is_(True))
            .order_by(Test.created_at.desc())
        ).unique()
    )
    return [serialize_test(test, include_rules=False) for test in tests]


@router.get("/attempts/active")
def active_attempt(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict | None:
    attempt = db.scalar(
        select(Attempt)
        .options(selectinload(Attempt.questions), selectinload(Attempt.test))
        .where(Attempt.user_id == user.id, Attempt.finished_at.is_(None))
        .order_by(Attempt.started_at.desc())
    )
    if not attempt:
        return None
    auto_finish_if_expired(db, attempt)
    if attempt.finished_at:
        return None
    return serialize_attempt(attempt)


@router.post("/attempts", status_code=201)
def start_attempt(payload: AttemptCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    test = db.scalar(
        select(Test)
        .options(
            selectinload(Test.rules).selectinload(TestRule.source).selectinload(Source.questions).selectinload(Question.answers)
        )
        .where(Test.id == payload.test_id, Test.is_active.is_(True))
    )
    if not test:
        raise HTTPException(status_code=404, detail="Faol test topilmadi")
    attempt = create_attempt(db, user, test)
    return serialize_attempt(attempt)


@router.get("/attempts/{attempt_id}")
def attempt_detail(attempt_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    attempt = get_attempt(db, attempt_id, user.id)
    auto_finish_if_expired(db, attempt)
    return serialize_attempt(attempt)


@router.post("/attempts/{attempt_id}/answer")
def answer_question(
    attempt_id: int,
    payload: AnswerSubmit,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    attempt = get_attempt(db, attempt_id, user.id)
    return submit_answer(db, attempt, payload.question_id, payload.answer_id)


@router.post("/attempts/{attempt_id}/finish")
def finish(attempt_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    attempt = get_attempt(db, attempt_id, user.id)
    return finish_attempt(db, attempt)


@router.get("/attempts/{attempt_id}/review")
def review(attempt_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    attempt = get_attempt(db, attempt_id, user.id)
    return review_attempt(attempt)

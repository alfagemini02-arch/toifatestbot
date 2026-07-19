from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app import models
from backend.app.database import SessionLocal
from backend.app.seed import seed_demo_data
from backend.app.services import create_attempt, finish_attempt, serialize_attempt, submit_answer


def test_attempt_hides_answers_and_finishes() -> None:
    seed_demo_data()
    with SessionLocal() as db:
        user = models.User(telegram_id=880011, full_name='Sinovchi', phone='+998900000000')
        db.add(user)
        db.commit()
        db.refresh(user)
        test = db.scalar(
            select(models.Test).options(
                selectinload(models.Test.rules)
                .selectinload(models.TestRule.source)
                .selectinload(models.Source.questions)
                .selectinload(models.Question.answers)
            )
        )
        assert test is not None
        attempt = create_attempt(db, user, test)
        payload = serialize_attempt(attempt)
        assert payload['total_questions'] == 3
        assert all('correct' not in answer for question in payload['questions'] for answer in question['answers'])

        first = attempt.questions[0]
        correct = next(answer for answer in first.answers_snapshot if answer['correct'])
        result = submit_answer(db, attempt, first.question_id or first.id, int(correct['id']))
        assert result['is_correct'] is True
        finished = finish_attempt(db, attempt)
        assert finished['answered'] == 1
        assert finished['correct'] == 1

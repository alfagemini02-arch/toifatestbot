from __future__ import annotations

from collections import defaultdict
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .database import get_db
from .deps import get_current_admin
from .importers import ParsedQuestion, parse_uploaded_file
from .models import Answer, Attempt, ErrorReport, Question, Source, Test, TestAttemptStat, TestRule, utcnow
from .schemas import (
    ImportCommitRequest,
    QuestionBulkDeleteRequest,
    QuestionInput,
    QuestionMoveRequest,
    ReportStatusUpdate,
    SourceCreate,
    SourceUpdate,
    TestInput,
)
from .services import admin_dashboard_stats, serialize_question, serialize_test

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])
settings = get_settings()


def _duplicate_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _source_or_404(db: Session, source_id: int) -> Source:
    source = db.scalar(select(Source).where(Source.id == source_id))
    if not source:
        raise HTTPException(status_code=404, detail="Manba topilmadi")
    return source


def _question_or_404(db: Session, question_id: int) -> Question:
    question = db.scalar(
        select(Question)
        .options(selectinload(Question.answers), selectinload(Question.source))
        .where(Question.id == question_id)
    )
    if not question:
        raise HTTPException(status_code=404, detail="Savol topilmadi")
    return question


def _test_or_404(db: Session, test_id: int) -> Test:
    test = db.scalar(
        select(Test)
        .options(
            selectinload(Test.rules)
            .selectinload(TestRule.source)
            .selectinload(Source.questions)
        )
        .where(Test.id == test_id)
    )
    if not test:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


def _report_or_404(db: Session, report_id: int) -> ErrorReport:
    report = db.scalar(select(ErrorReport).options(selectinload(ErrorReport.user)).where(ErrorReport.id == report_id))
    if not report:
        raise HTTPException(status_code=404, detail="Xatolik xabari topilmadi")
    return report


def _serialize_report(db: Session, report: ErrorReport) -> dict:
    question_payload = None
    if report.question_id:
        question = db.scalar(
            select(Question)
            .options(selectinload(Question.answers), selectinload(Question.source))
            .where(Question.id == report.question_id)
        )
        if question:
            question_payload = serialize_question(question)
    return {
        "id": report.id,
        "status": report.status,
        "message_text": report.message_text,
        "created_at": report.created_at.isoformat(),
        "fixed_at": report.fixed_at.isoformat() if report.fixed_at else None,
        "attempt_id": report.attempt_id,
        "question_id": report.question_id,
        "question_text": question_payload["question_text"] if question_payload else report.question_text_snapshot,
        "source_name": question_payload["source_name"] if question_payload else report.source_name_snapshot,
        "answers": question_payload["answers"] if question_payload else (report.answers_snapshot or []),
        "question": question_payload,
        "user": {
            "id": report.user.id if report.user else None,
            "full_name": report.user.full_name if report.user else None,
            "telegram_id": report.user.telegram_id if report.user else None,
            "phone": report.user.phone if report.user else None,
            "username": report.user.username if report.user else None,
        },
    }


@router.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    return admin_dashboard_stats(db)


@router.get("/reports")
def list_reports(
    status: str = "open",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 30,
    db: Session = Depends(get_db),
) -> dict:
    query = select(ErrorReport).options(selectinload(ErrorReport.user))
    count_query = select(func.count(ErrorReport.id))
    if status != "all":
        query = query.where(ErrorReport.status == status)
        count_query = count_query.where(ErrorReport.status == status)
    total = db.scalar(count_query) or 0
    reports = list(
        db.scalars(query.order_by(ErrorReport.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    )
    return {
        "items": [_serialize_report(db, report) for report in reports],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.put("/reports/{report_id}/status")
def update_report_status(report_id: int, payload: ReportStatusUpdate, db: Session = Depends(get_db)) -> dict:
    report = _report_or_404(db, report_id)
    report.status = payload.status
    report.fixed_at = utcnow() if payload.status == "fixed" else None
    db.commit()
    return _serialize_report(db, report)


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, db: Session = Depends(get_db)) -> dict:
    report = _report_or_404(db, report_id)
    db.delete(report)
    db.commit()
    return {"deleted": True}


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    question_count = select(func.count(Question.id)).where(Question.source_id == Source.id).correlate(Source).scalar_subquery()
    rule_count = select(func.count(TestRule.id)).where(TestRule.source_id == Source.id).correlate(Source).scalar_subquery()
    rows = db.execute(select(Source, question_count.label("question_count"), rule_count.label("rule_count")).order_by(Source.name)).all()
    return [
        {
            "id": source.id,
            "name": source.name,
            "created_at": source.created_at.isoformat(),
            "question_count": count,
            "used_in_tests": used,
        }
        for source, count, used in rows
    ]


@router.post("/sources", status_code=201)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> dict:
    source = Source(name=payload.name.strip())
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bunday nomli manba mavjud") from exc
    db.refresh(source)
    return {"id": source.id, "name": source.name}


@router.put("/sources/{source_id}")
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)) -> dict:
    source = _source_or_404(db, source_id)
    source.name = payload.name.strip()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bunday nomli manba mavjud") from exc
    return {"id": source.id, "name": source.name}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db)) -> dict:
    source = _source_or_404(db, source_id)
    used = db.scalar(select(func.count(TestRule.id)).where(TestRule.source_id == source_id)) or 0
    if used:
        tests = list(
            db.scalars(
                select(Test.name).join(TestRule, TestRule.test_id == Test.id).where(TestRule.source_id == source_id)
            )
        )
        raise HTTPException(status_code=409, detail=f"Manba testlarda ishlatilmoqda: {', '.join(tests)}")
    db.delete(source)
    db.commit()
    return {"deleted": True}


@router.get("/sources/{source_id}/questions")
def source_questions(
    source_id: int,
    search: str = "",
    topic: str = "",
    difficulty: str = "",
    answer_count: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=0, le=5000)] = 20,
    db: Session = Depends(get_db),
) -> dict:
    source = _source_or_404(db, source_id)
    answer_count_subquery = select(func.count(Answer.id)).where(Answer.question_id == Question.id).correlate(Question).scalar_subquery()
    query = select(Question).options(selectinload(Question.answers), selectinload(Question.source)).where(Question.source_id == source_id)
    count_query = select(func.count(func.distinct(Question.id))).select_from(Question).outerjoin(Answer).where(Question.source_id == source_id)
    if search.strip():
        pattern = f"%{search.strip()}%"
        condition = or_(Question.question_text.ilike(pattern), Answer.answer_text.ilike(pattern))
        query = query.outerjoin(Answer).where(condition).distinct()
        count_query = count_query.where(condition)
    if topic.strip():
        query = query.where(Question.topic == topic.strip())
        count_query = count_query.where(Question.topic == topic.strip())
    if difficulty.strip():
        query = query.where(Question.difficulty == difficulty.strip())
        count_query = count_query.where(Question.difficulty == difficulty.strip())
    parsed_answer_count = int(answer_count) if answer_count.strip().isdigit() else None
    if parsed_answer_count:
        query = query.where(answer_count_subquery == parsed_answer_count)
        count_query = count_query.where(answer_count_subquery == parsed_answer_count)
    total = db.scalar(count_query) or 0
    ordered = query.order_by(Question.id.desc())
    if page_size:
        ordered = ordered.offset((page - 1) * page_size).limit(page_size)
    questions = list(db.scalars(ordered).unique())
    topics = [value for value in db.scalars(select(Question.topic).where(Question.source_id == source_id, Question.topic.is_not(None)).distinct().order_by(Question.topic))]
    answer_count_rows = db.execute(
        select(func.count(Answer.id).label("answer_count"))
        .select_from(Question)
        .join(Answer, Answer.question_id == Question.id)
        .where(Question.source_id == source_id)
        .group_by(Question.id)
    ).all()
    answer_counts = sorted({row[0] for row in answer_count_rows})
    return {
        "source": {"id": source.id, "name": source.name},
        "items": [serialize_question(question) for question in questions],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": 1 if not page_size else max(1, (total + page_size - 1) // page_size),
        "filters": {"topics": topics, "difficulties": ["easy", "medium", "hard"], "answer_counts": answer_counts},
    }


@router.get("/sources/{source_id}/duplicates")
def source_duplicates(source_id: int, db: Session = Depends(get_db)) -> dict:
    source = _source_or_404(db, source_id)
    questions = list(
        db.scalars(
            select(Question)
            .options(selectinload(Question.answers), selectinload(Question.source))
            .where(Question.source_id == source_id)
            .order_by(Question.id)
        ).unique()
    )
    groups: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        groups[_duplicate_key(question.question_text)].append(question)
    duplicate_groups = [
        {
            "key": key,
            "count": len(items),
            "keep_id": items[0].id,
            "items": [serialize_question(item) for item in items],
        }
        for key, items in groups.items()
        if len(items) > 1
    ]
    duplicate_groups.sort(key=lambda group: group["count"], reverse=True)
    return {
        "source": {"id": source.id, "name": source.name},
        "groups": duplicate_groups,
        "group_count": len(duplicate_groups),
        "duplicate_question_count": sum(group["count"] for group in duplicate_groups),
        "delete_candidate_count": sum(group["count"] - 1 for group in duplicate_groups),
    }


@router.post("/sources/{source_id}/duplicates/deduplicate")
def deduplicate_source(source_id: int, db: Session = Depends(get_db)) -> dict:
    _source_or_404(db, source_id)
    questions = list(
        db.scalars(select(Question).where(Question.source_id == source_id).order_by(Question.id))
    )
    groups: dict[str, list[Question]] = defaultdict(list)
    for question in questions:
        groups[_duplicate_key(question.question_text)].append(question)

    delete_ids: list[int] = []
    for items in groups.values():
        if len(items) > 1:
            delete_ids.extend(question.id for question in items[1:])
    if delete_ids:
        db.execute(delete(Question).where(Question.id.in_(delete_ids)))
        db.commit()
    return {"deleted": len(delete_ids), "kept": sum(1 for items in groups.values() if len(items) > 1)}


@router.get("/questions/{question_id}")
def get_question(question_id: int, db: Session = Depends(get_db)) -> dict:
    return serialize_question(_question_or_404(db, question_id))


@router.post("/questions", status_code=201)
def create_question(payload: QuestionInput, db: Session = Depends(get_db)) -> dict:
    _source_or_404(db, payload.source_id)
    question = Question(
        source_id=payload.source_id,
        question_text=payload.question_text.strip(),
        topic=payload.topic.strip() if payload.topic else None,
        difficulty=payload.difficulty,
        explanation=payload.explanation.strip() if payload.explanation else None,
    )
    db.add(question)
    db.flush()
    for position, answer in enumerate(payload.answers):
        db.add(Answer(question_id=question.id, answer_text=answer.text.strip(), is_correct=answer.correct, position=position))
    db.commit()
    return serialize_question(_question_or_404(db, question.id))


@router.put("/questions/{question_id}")
def update_question(question_id: int, payload: QuestionInput, db: Session = Depends(get_db)) -> dict:
    question = _question_or_404(db, question_id)
    _source_or_404(db, payload.source_id)
    question.source_id = payload.source_id
    question.question_text = payload.question_text.strip()
    question.topic = payload.topic.strip() if payload.topic else None
    question.difficulty = payload.difficulty
    question.explanation = payload.explanation.strip() if payload.explanation else None
    db.execute(delete(Answer).where(Answer.question_id == question.id))
    for position, answer in enumerate(payload.answers):
        db.add(Answer(question_id=question.id, answer_text=answer.text.strip(), is_correct=answer.correct, position=position))
    db.commit()
    return serialize_question(_question_or_404(db, question.id))


@router.delete("/questions/{question_id}")
def delete_question(question_id: int, db: Session = Depends(get_db)) -> dict:
    question = _question_or_404(db, question_id)
    db.delete(question)
    db.commit()
    return {"deleted": True}


@router.post("/questions/bulk-delete")
def bulk_delete_questions(payload: QuestionBulkDeleteRequest, db: Session = Depends(get_db)) -> dict:
    existing_ids = set(db.scalars(select(Question.id).where(Question.id.in_(payload.question_ids))))
    missing = [question_id for question_id in payload.question_ids if question_id not in existing_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"{len(missing)} ta savol topilmadi")
    db.execute(delete(Question).where(Question.id.in_(payload.question_ids)))
    db.commit()
    return {"deleted": len(payload.question_ids)}


@router.post("/questions/move")
def move_questions(payload: QuestionMoveRequest, db: Session = Depends(get_db)) -> dict:
    target_source = _source_or_404(db, payload.target_source_id)
    questions = list(db.scalars(select(Question).where(Question.id.in_(payload.question_ids))))
    found_ids = {question.id for question in questions}
    missing = [question_id for question_id in payload.question_ids if question_id not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"{len(missing)} ta savol topilmadi")

    moved = 0
    for question in questions:
        if question.source_id != target_source.id:
            question.source_id = target_source.id
            moved += 1
    db.commit()
    return {"moved": moved, "target_source": {"id": target_source.id, "name": target_source.name}}


@router.get("/search")
def global_search(
    q: Annotated[str, Query(min_length=1)],
    source_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    db: Session = Depends(get_db),
) -> dict:
    pattern = f"%{q.strip()}%"
    condition = or_(Question.question_text.ilike(pattern), Answer.answer_text.ilike(pattern))
    base = select(Question).options(selectinload(Question.answers), selectinload(Question.source)).outerjoin(Answer).where(condition)
    count_query = select(func.count(func.distinct(Question.id))).select_from(Question).outerjoin(Answer).where(condition)
    if source_id:
        base = base.where(Question.source_id == source_id)
        count_query = count_query.where(Question.source_id == source_id)
    total = db.scalar(count_query) or 0
    items = list(
        db.scalars(base.distinct().order_by(Question.id.desc()).offset((page - 1) * page_size).limit(page_size)).unique()
    )
    return {"items": [serialize_question(item) for item in items], "total": total, "page": page, "page_size": page_size}


def _validate_test_rules(db: Session, payload: TestInput) -> None:
    for rule in payload.rules:
        source = _source_or_404(db, rule.source_id)
        available = db.scalar(select(func.count(Question.id)).where(Question.source_id == source.id)) or 0
        if rule.question_count > available:
            raise HTTPException(
                status_code=422,
                detail=f"'{source.name}' manbasida {available} ta savol bor, {rule.question_count} ta so'ralgan",
            )


def _write_test_rules(db: Session, test: Test, payload: TestInput) -> None:
    db.execute(delete(TestRule).where(TestRule.test_id == test.id))
    for rule in payload.rules:
        db.add(TestRule(test_id=test.id, source_id=rule.source_id, question_count=rule.question_count))


@router.get("/tests")
def list_admin_tests(db: Session = Depends(get_db)) -> list[dict]:
    attempts_count = select(func.count(TestAttemptStat.id)).where(TestAttemptStat.test_id == Test.id).correlate(Test).scalar_subquery()
    tests = list(
        db.scalars(
            select(Test)
            .options(selectinload(Test.rules).selectinload(TestRule.source).selectinload(Source.questions))
            .order_by(Test.created_at.desc())
        ).unique()
    )
    counts = {row[0]: row[1] for row in db.execute(select(Test.id, attempts_count)).all()}
    return [{**serialize_test(test), "attempt_count": counts.get(test.id, 0)} for test in tests]


@router.get("/tests/{test_id}")
def get_admin_test(test_id: int, db: Session = Depends(get_db)) -> dict:
    return serialize_test(_test_or_404(db, test_id))


@router.post("/tests", status_code=201)
def create_test(payload: TestInput, db: Session = Depends(get_db)) -> dict:
    _validate_test_rules(db, payload)
    test = Test(name=payload.name.strip(), test_mode=payload.test_mode, time_limit_minutes=payload.time_limit_minutes, is_active=payload.is_active)
    db.add(test)
    try:
        db.flush()
        _write_test_rules(db, test, payload)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bunday nomli test mavjud") from exc
    return serialize_test(_test_or_404(db, test.id))


@router.put("/tests/{test_id}")
def update_test(test_id: int, payload: TestInput, db: Session = Depends(get_db)) -> dict:
    test = _test_or_404(db, test_id)
    _validate_test_rules(db, payload)
    test.name = payload.name.strip()
    test.test_mode = payload.test_mode
    test.time_limit_minutes = payload.time_limit_minutes
    test.is_active = payload.is_active
    try:
        _write_test_rules(db, test, payload)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bunday nomli test mavjud") from exc
    return serialize_test(_test_or_404(db, test.id))


@router.delete("/tests/{test_id}")
def delete_test(test_id: int, db: Session = Depends(get_db)) -> dict:
    test = _test_or_404(db, test_id)
    db.execute(delete(Attempt).where(Attempt.test_id == test_id))
    db.execute(delete(TestAttemptStat).where(TestAttemptStat.test_id == test_id))
    db.delete(test)
    db.commit()
    return {"deleted": True}


@router.post("/import/parse")
async def parse_import(
    file: Annotated[UploadFile, File(...)],
    source_id: Annotated[int | None, Form()] = None,
    db_mode: Annotated[str, Form()] = "single",
    db: Session = Depends(get_db),
) -> dict:
    filename = file.filename or ""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix not in {"txt", "docx", "db", "sqlite", "sqlite3"}:
        raise HTTPException(status_code=415, detail="Faqat .txt, .docx va .db fayllar qabul qilinadi")
    content = await file.read(settings.upload_limit_bytes + 1)
    if len(content) > settings.upload_limit_bytes:
        raise HTTPException(status_code=413, detail=f"Fayl hajmi {settings.max_upload_mb} MB dan oshmasligi kerak")
    if source_id:
        _source_or_404(db, source_id)
    try:
        parsed = parse_uploaded_file(filename, content, db_mode=db_mode)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if source_id:
        existing = {
            text.casefold()
            for text in db.scalars(select(Question.question_text).where(Question.source_id == source_id))
        }
        for item in parsed:
            item.duplicate_in_database = item.question.casefold() in existing
    elif db_mode == "full":
        source_groups: dict[str, set[str]] = defaultdict(set)
        for source_name, question_text in db.execute(select(Source.name, Question.question_text).join(Question, Question.source_id == Source.id)).all():
            source_groups[source_name.casefold()].add(question_text.casefold())
        for item in parsed:
            if item.source_name:
                item.duplicate_in_database = item.question.casefold() in source_groups[item.source_name.casefold()]

    valid_count = sum(1 for item in parsed if item.valid)
    return {
        "parsed": [item.to_dict() for item in parsed],
        "stats": {
            "total": len(parsed),
            "valid": valid_count,
            "problematic": len(parsed) - valid_count,
            "duplicates_in_file": sum(1 for item in parsed if item.duplicate_in_file),
            "duplicates_in_database": sum(1 for item in parsed if item.duplicate_in_database),
        },
    }


def _resolve_import_source(db: Session, requested_name: str) -> Source:
    base_name = requested_name.strip() or "Import"
    source = db.scalar(select(Source).where(func.lower(Source.name) == base_name.lower()))
    if source:
        return source
    source = Source(name=base_name)
    db.add(source)
    db.flush()
    return source


@router.post("/import/commit")
def commit_import(payload: ImportCommitRequest, db: Session = Depends(get_db)) -> dict:
    if not payload.create_sources_from_file and not payload.source_id and not payload.new_source_name:
        raise HTTPException(status_code=422, detail="Manba tanlang yoki yangi manba nomini kiriting")

    default_source: Source | None = None
    if not payload.create_sources_from_file:
        default_source = _source_or_404(db, payload.source_id) if payload.source_id else _resolve_import_source(db, payload.new_source_name or "Import")

    added = 0
    skipped = 0
    source_cache: dict[str, Source] = {}
    for item in payload.questions:
        parsed = ParsedQuestion(
            question=item.question.strip(),
            answers=[],
            valid=item.valid,
            problems=list(item.problems),
            duplicate_in_file=item.duplicate_in_file,
            duplicate_in_database=item.duplicate_in_database,
            source_name=item.source_name,
        )
        if not parsed.valid or len(item.answers) < 2 or sum(1 for answer in item.answers if answer.correct) != 1:
            skipped += 1
            continue
        if payload.skip_duplicates and (item.duplicate_in_file or item.duplicate_in_database):
            skipped += 1
            continue

        if payload.create_sources_from_file:
            source_name = (item.source_name or payload.new_source_name or "Import").strip()
            key = source_name.casefold()
            source = source_cache.get(key)
            if not source:
                source = _resolve_import_source(db, source_name)
                source_cache[key] = source
        else:
            source = default_source
        assert source is not None

        if payload.skip_duplicates:
            duplicate = db.scalar(
                select(Question.id).where(
                    Question.source_id == source.id,
                    func.lower(Question.question_text) == item.question.strip().lower(),
                )
            )
            if duplicate:
                skipped += 1
                continue

        question = Question(source_id=source.id, question_text=item.question.strip())
        db.add(question)
        db.flush()
        for position, answer in enumerate(item.answers):
            db.add(
                Answer(
                    question_id=question.id,
                    answer_text=answer.text.strip(),
                    is_correct=answer.correct,
                    position=position,
                )
            )
        added += 1
    db.commit()
    return {"added": added, "skipped": skipped}

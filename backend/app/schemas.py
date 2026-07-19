from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class TelegramAuthRequest(BaseModel):
    init_data: str = ""
    webapp_token: str = ""


class DevAuthRequest(BaseModel):
    telegram_id: int | None = None
    full_name: str = "Test Foydalanuvchi"
    phone: str = "+998000000000"


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class SourceCreate(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=255)]


class SourceUpdate(SourceCreate):
    pass


class AnswerInput(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=5000)]
    correct: bool = False


class QuestionInput(BaseModel):
    source_id: int
    question_text: Annotated[str, Field(min_length=2, max_length=20000)]
    answers: Annotated[list[AnswerInput], Field(min_length=2, max_length=20)]

    @model_validator(mode="after")
    def validate_answers(self) -> QuestionInput:
        if sum(1 for item in self.answers if item.correct) != 1:
            raise ValueError("Aynan bitta to'g'ri javob tanlanishi kerak")
        normalized = [item.text.strip().casefold() for item in self.answers]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Bir xil javob varianti takrorlangan")
        return self


class QuestionMoveRequest(BaseModel):
    question_ids: Annotated[list[int], Field(min_length=1, max_length=1000)]
    target_source_id: int

    @field_validator("question_ids")
    @classmethod
    def unique_question_ids(cls, question_ids: list[int]) -> list[int]:
        unique_ids = list(dict.fromkeys(question_ids))
        if any(question_id <= 0 for question_id in unique_ids):
            raise ValueError("Savol ID noto'g'ri")
        return unique_ids


class TestRuleInput(BaseModel):
    source_id: int
    question_count: Annotated[int, Field(ge=1, le=500)]


class TestInput(BaseModel):
    name: Annotated[str, Field(min_length=2, max_length=255)]
    time_limit_minutes: Annotated[int, Field(ge=0, le=1440)] = 0
    is_active: bool = True
    rules: Annotated[list[TestRuleInput], Field(min_length=1, max_length=100)]

    @field_validator("rules")
    @classmethod
    def unique_sources(cls, rules: list[TestRuleInput]) -> list[TestRuleInput]:
        ids = [rule.source_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("Bitta manba testda ikki marta tanlanmasligi kerak")
        return rules


class AttemptCreate(BaseModel):
    test_id: int


class AnswerSubmit(BaseModel):
    question_id: int
    answer_id: int


class QuestionReportInput(BaseModel):
    message: Annotated[str, Field(min_length=3, max_length=2000)]
    attempt_id: int | None = None


class ReportStatusUpdate(BaseModel):
    status: Annotated[str, Field(pattern="^(open|fixed)$")]


class ImportAnswer(BaseModel):
    text: str
    correct: bool = False


class ImportQuestion(BaseModel):
    question: str
    answers: list[ImportAnswer]
    valid: bool = True
    problems: list[str] = Field(default_factory=list)
    duplicate_in_file: bool = False
    duplicate_in_database: bool = False
    source_name: str | None = None


class ImportCommitRequest(BaseModel):
    source_id: int | None = None
    new_source_name: str | None = None
    create_sources_from_file: bool = False
    skip_duplicates: bool = True
    questions: list[ImportQuestion]

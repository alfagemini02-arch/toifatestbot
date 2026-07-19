from __future__ import annotations

from sqlalchemy import select

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Admin, Answer, Question, Source, Test, TestRule
from .security import hash_password

settings = get_settings()


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)
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

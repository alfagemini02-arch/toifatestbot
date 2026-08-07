from __future__ import annotations

import asyncio
import html
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    MenuButtonWebApp,
    Message,
    PollAnswer,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from sqlalchemy import delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .config import get_settings
from .database import SessionLocal
from .models import (
    Broadcast,
    Answer,
    ErrorReport,
    GroupQuizAnswer,
    GroupQuizQuestion,
    GroupQuizSession,
    GroupQuizVote,
    Question,
    TelegramGroup,
    Test,
    TestRule,
    User,
    utcnow,
)
from .security import create_webapp_login_token
from .services import admin_dashboard_stats

logger = logging.getLogger(__name__)
settings = get_settings()
TASHKENT = ZoneInfo("Asia/Tashkent")
GROUP_CHAT_TYPES = {"group", "supergroup"}
GROUP_QUIZ_MAX_TESTS = 25
GROUP_QUIZ_LIVE_STATUSES = ("pending_start", "starting", "active", "stopping")
TELEGRAM_ALLOWED_UPDATES = ["message", "callback_query", "poll_answer", "my_chat_member"]

bot: Bot | None = None
dp: Dispatcher | None = None
router = Router()
resolved_bot_username = settings.bot_username.lstrip("@").casefold()


class Registration(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


class ReportState(StatesGroup):
    waiting_report = State()


class BroadcastState(StatesGroup):
    waiting_message = State()
    waiting_confirmation = State()


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_set


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^\w\s']", "", value, flags=re.UNICODE).strip().casefold()


def button_is(*labels: str):  # noqa: ANN201
    normalized = {plain_text(label) for label in labels}
    return F.text.func(lambda value: plain_text(value) in normalized)


def addressed_group_command_is(*commands: str):  # noqa: ANN201
    expected = {command.casefold() for command in commands}

    def matches(value: str | None) -> bool:
        if not value or not resolved_bot_username:
            return False
        match = re.fullmatch(r"/([a-z0-9_]+)@([a-z0-9_]+)(?:\s+.*)?", value.strip(), flags=re.IGNORECASE)
        return bool(match and match.group(1).casefold() in expected and match.group(2).casefold() == resolved_bot_username)

    return F.text.func(matches)


def normalized_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def webapp_entry_url() -> str:
    base_url = settings.normalized_webapp_url
    if base_url.endswith("/app"):
        return f"{base_url}/"
    if base_url.endswith("/app/"):
        return base_url
    return f"{base_url}/app/"


def user_webapp_url(telegram_id: int | None = None) -> str:
    webapp_url = webapp_entry_url()
    if telegram_id:
        separator = "&" if "?" in webapp_url else "?"
        webapp_url = f"{webapp_url}{separator}tg_login={create_webapp_login_token(telegram_id)}"
    return webapp_url


async def set_user_menu_button(message: Message) -> None:
    if not message.from_user or is_group_chat(message):
        return
    try:
        await message.bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(text="Testlarni boshlash", web_app=WebAppInfo(url=user_webapp_url(message.from_user.id))),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Telegram menu button sozlanmadi")


def main_menu(admin: bool = False, telegram_id: int | None = None) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="Testlarni boshlash", web_app=WebAppInfo(url=user_webapp_url(telegram_id)))],
        [KeyboardButton(text="Xatolik haqida xabar")],
        [KeyboardButton(text="Admin bilan aloqa")],
    ]
    if admin:
        rows.append([KeyboardButton(text="Batafsil statistika"), KeyboardButton(text="Barchaga xabar")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Kerakli bo'limni tanlang")


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_user_by_tg(telegram_id: int) -> User | None:
    with SessionLocal() as db:
        return db.scalar(select(User).where(User.telegram_id == telegram_id))


def touch_user(telegram_id: int) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == telegram_id))
        if user:
            user.last_active_at = utcnow()
            db.commit()


def is_group_chat(message: Message | None) -> bool:
    return bool(message and message.chat and message.chat.type in GROUP_CHAT_TYPES)


def limit_poll_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def active_group_or_none(chat_id: int, title: str | None = None) -> TelegramGroup | None:
    with SessionLocal() as db:
        group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
        if group:
            group.last_seen_at = utcnow()
            if title and not group.title:
                group.title = title
            db.commit()
            db.refresh(group)
        if not group or not group.is_allowed:
            return None
        return group


async def user_can_manage_group_quiz(message: Message | None, user_id: int | None) -> bool:
    if not message or not user_id:
        return False
    if is_admin(user_id):
        return True
    try:
        member = await message.bot.get_chat_member(message.chat.id, user_id)
    except Exception:  # noqa: BLE001
        return False
    return member.status in {"administrator", "creator"}


def group_quiz_test_keyboard(tests: list[Test]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{test.name} ({sum(rule.question_count for rule in test.rules)} ta)", callback_data=f"gquiz:start:{test.id}")]
        for test in tests[:GROUP_QUIZ_MAX_TESTS]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_vote_keyboard(session_id: int, vote_type: str, count: int, required: int) -> InlineKeyboardMarkup:
    label = "🚀 Boshlash uchun ovoz" if vote_type == "start" else "🛑 To'xtatish uchun ovoz"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{label} · {count}/{required}", callback_data=f"gquiz:vote_{vote_type}:{session_id}")]
        ]
    )


def live_group_quiz_session(db, chat_id: int) -> GroupQuizSession | None:  # noqa: ANN001
    return db.scalar(
        select(GroupQuizSession)
        .where(GroupQuizSession.chat_id == chat_id, GroupQuizSession.status.in_(GROUP_QUIZ_LIVE_STATUSES))
        .order_by(GroupQuizSession.id.desc())
        .limit(1)
    )


def format_vote_window(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60} daqiqa"
    return f"{seconds} soniya"


def profile_link(user_id: int, username: str | None, full_name: str | None, max_length: int = 48) -> str:
    display_name = limit_poll_text(full_name or (f"@{username}" if username else str(user_id)), max_length)
    label = html.escape(display_name)
    if username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return f'<a href="https://t.me/{username}">{label}</a>'
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def make_group_quiz_snapshot(question: Question) -> tuple[list[dict[str, object]], int]:
    answers = [
        {"id": answer.id, "text": answer.answer_text.strip(), "correct": answer.is_correct}
        for answer in question.answers
    ]
    correct = next((answer for answer in answers if answer["correct"]), None)
    if not correct:
        raise ValueError("Savolda to'g'ri javob belgilanmagan")
    incorrect = [answer for answer in answers if not answer["correct"]]
    random.shuffle(incorrect)
    selected = [correct, *incorrect[:9]]
    if len(selected) < 2:
        raise ValueError("Telegram quiz uchun kamida 2 ta variant kerak")
    random.shuffle(selected)
    correct_option_id = next(index for index, answer in enumerate(selected) if answer["correct"])
    return selected, correct_option_id


def group_quiz_poll_content(
    order_index: int,
    total: int,
    question_text: str,
    options: list[str],
) -> tuple[str, list[str], str | None]:
    normalized_question = re.sub(r"\s+", " ", question_text).strip()
    normalized_options = [re.sub(r"\s+", " ", option).strip() for option in options]
    regular_question = f"{order_index}/{total}. {normalized_question}"
    if len(regular_question) <= 300 and all(len(option) <= 100 for option in normalized_options):
        return regular_question, normalized_options, None

    labels = [chr(ord("A") + index) for index in range(len(normalized_options))]
    expanded_lines = [regular_question, ""]
    expanded_lines.extend(f"{label}) {option}" for label, option in zip(labels, normalized_options, strict=True))
    expanded_question = "\n".join(expanded_lines)
    if len(expanded_question) <= 300:
        return expanded_question, labels, None

    poll_question = f"{order_index}/{total}. Yuqoridagi savol uchun to'g'ri javobni tanlang."
    return poll_question, labels, expanded_question


async def send_group_quiz_context(bot_obj: Bot, chat_id: int, text: str) -> None:
    remaining = text
    while remaining:
        if len(remaining) <= 4000:
            chunk, remaining = remaining, ""
        else:
            split_at = remaining.rfind("\n", 0, 4000)
            if split_at < 1000:
                split_at = 4000
            chunk, remaining = remaining[:split_at], remaining[split_at:].lstrip("\n")
        await bot_obj.send_message(chat_id, chunk, parse_mode=None)


def load_group_quiz_test(db, test_id: int) -> Test | None:  # noqa: ANN001
    return db.scalar(
        select(Test)
        .options(selectinload(Test.rules))
        .where(Test.id == test_id, Test.is_active.is_(True), Test.test_mode == "exam")
    )


async def send_group_quiz_question(bot_obj: Bot, session_id: int) -> None:
    should_finish = False
    with SessionLocal() as db:
        session = db.scalar(
            select(GroupQuizSession)
            .options(selectinload(GroupQuizSession.questions))
            .where(GroupQuizSession.id == session_id, GroupQuizSession.status == "active")
        )
        if not session:
            return
        question = next((item for item in session.questions if item.order_index == session.current_index + 1), None)
        if not question:
            session.status = "stopping"
            session.finished_at = utcnow()
            db.commit()
            should_finish = True
        else:
            question_id = question.id
            chat_id = session.chat_id
            seconds = session.question_seconds
            title = session.test_name_snapshot
            order_index = question.order_index
            total = session.total_questions
            question_text = question.question_text_snapshot
            options = [str(answer["text"]) for answer in question.answers_snapshot]
            correct_option_id = question.correct_option_id

    if should_finish:
        await send_group_quiz_results(bot_obj, session_id)
        return

    try:
        poll_question, poll_options, context_text = group_quiz_poll_content(
            order_index,
            total,
            question_text,
            options,
        )
        if context_text:
            await send_group_quiz_context(bot_obj, chat_id, context_text)
        poll_message = await bot_obj.send_poll(
            chat_id=chat_id,
            question=poll_question,
            options=poll_options,
            type="quiz",
            correct_option_id=correct_option_id,
            is_anonymous=False,
            open_period=seconds,
            explanation=limit_poll_text(title, 200),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Guruh quiz savoli yuborilmadi")
        asyncio.create_task(advance_group_quiz_later(bot_obj, session_id, 1))
        return

    with SessionLocal() as db:
        question_row = db.scalar(select(GroupQuizQuestion).where(GroupQuizQuestion.id == question_id))
        if question_row:
            question_row.poll_id = poll_message.poll.id if poll_message.poll else None
            question_row.message_id = poll_message.message_id
            question_row.sent_at = utcnow()
            db.commit()
    asyncio.create_task(advance_group_quiz_later(bot_obj, session_id, seconds))


async def prepare_group_quiz_session(bot_obj: Bot, session_id: int) -> bool:
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id).with_for_update())
        if not session or session.status != "starting":
            return False
        test = load_group_quiz_test(db, session.test_id or 0)
        if not test:
            chat_id = session.chat_id
            db.delete(session)
            db.commit()
            await bot_obj.send_message(chat_id, "Test topilmadi yoki endi faol emas.")
            return False

        selected_questions: list[Question] = []
        for rule in test.rules:
            has_correct = exists(
                select(Answer.id).where(Answer.question_id == Question.id, Answer.is_correct.is_(True))
            )
            has_incorrect = exists(
                select(Answer.id).where(Answer.question_id == Question.id, Answer.is_correct.is_(False))
            )
            questions = list(
                db.scalars(
                    select(Question)
                    .options(selectinload(Question.answers))
                    .where(Question.source_id == rule.source_id, has_correct, has_incorrect)
                    .order_by(func.random())
                    .limit(rule.question_count)
                ).unique()
            )
            selected_questions.extend(questions)
        random.shuffle(selected_questions)

        valid_count = 0
        for question in selected_questions:
            try:
                snapshot, correct_option_id = make_group_quiz_snapshot(question)
            except ValueError:
                continue
            valid_count += 1
            db.add(
                GroupQuizQuestion(
                    session_id=session.id,
                    question_id=question.id,
                    order_index=valid_count,
                    question_text_snapshot=question.question_text,
                    answers_snapshot=snapshot,
                    correct_option_id=correct_option_id,
                )
            )

        if valid_count == 0:
            chat_id = session.chat_id
            db.delete(session)
            db.commit()
            await bot_obj.send_message(chat_id, "Telegram quiz uchun yaroqli savol topilmadi.")
            return False

        session.total_questions = valid_count
        session.current_index = 0
        session.status = "active"
        session.started_at = utcnow()
        session.start_vote_deadline = None
        session.start_vote_message_id = None
        db.commit()
        chat_id = session.chat_id
        title = session.test_name_snapshot
        seconds = session.question_seconds

    await bot_obj.send_message(
        chat_id,
        "🚀 <b>QUIZ BOSHLANDI!</b>\n\n"
        f"📘 <b>{html.escape(title)}</b>\n"
        f"📝 Savollar: <b>{valid_count} ta</b>\n"
        f"⏱ Har savol: <b>{seconds} soniya</b>\n\n"
        "Javoblar shaxsiy hisoblanadi. Yakunda tezlik va to'g'ri javoblar bo'yicha reyting chiqadi. 🏆",
    )
    await send_group_quiz_question(bot_obj, session_id)
    return True


async def expire_start_vote(bot_obj: Bot, session_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds + 1)
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id).with_for_update())
        if not session or session.status != "pending_start":
            return
        deadline = normalized_utc(session.start_vote_deadline)
        if deadline and deadline > utcnow():
            return
        chat_id = session.chat_id
        message_id = session.start_vote_message_id
        required = session.start_vote_required
        count = db.scalar(select(func.count(GroupQuizVote.id)).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "start")) or 0
        db.delete(session)
        db.commit()
    if message_id:
        try:
            await bot_obj.edit_message_text(
                f"⌛ <b>Quiz boshlanmadi</b>\n\nKerakli {required} ta ovozdan {count} tasi yig'ildi. Keyingi /quiz@{resolved_bot_username} buyrug'ini kutaman.",
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Boshlash ovozi yakun xabari yangilanmadi")


async def expire_stop_vote(bot_obj: Bot, session_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds + 1)
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id).with_for_update())
        if not session or session.status != "active":
            return
        deadline = normalized_utc(session.stop_vote_deadline)
        if not deadline or deadline > utcnow():
            return
        chat_id = session.chat_id
        message_id = session.stop_vote_message_id
        required = session.stop_vote_required
        count = db.scalar(select(func.count(GroupQuizVote.id)).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "stop")) or 0
        db.execute(delete(GroupQuizVote).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "stop"))
        session.stop_vote_deadline = None
        session.stop_vote_message_id = None
        db.commit()
    if message_id:
        try:
            await bot_obj.edit_message_text(
                f"▶️ <b>Quiz davom etadi</b>\n\nTo'xtatish uchun kerakli {required} ta ovozdan {count} tasi yig'ildi.",
                chat_id=chat_id,
                message_id=message_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("To'xtatish ovozi yakun xabari yangilanmadi")


async def advance_group_quiz_later(bot_obj: Bot, session_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds + 3)
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id, GroupQuizSession.status == "active"))
        if not session:
            return
        next_index = session.current_index + 1
        if next_index >= session.total_questions:
            session.status = "stopping"
            session.finished_at = utcnow()
            db.commit()
            await send_group_quiz_results(bot_obj, session_id)
            return
        session.current_index = next_index
        db.commit()
    await send_group_quiz_question(bot_obj, session_id)


async def send_group_quiz_results(bot_obj: Bot, session_id: int, stopped_early: bool = False) -> None:
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id))
        if not session:
            return
        last_poll = db.scalar(
            select(GroupQuizQuestion)
            .where(GroupQuizQuestion.session_id == session_id, GroupQuizQuestion.message_id.is_not(None))
            .order_by(GroupQuizQuestion.order_index.desc())
            .limit(1)
        )
        answer_rows = db.execute(
            select(GroupQuizAnswer, GroupQuizQuestion.sent_at)
            .join(GroupQuizQuestion, GroupQuizQuestion.id == GroupQuizAnswer.quiz_question_id)
            .where(GroupQuizAnswer.session_id == session_id)
        ).all()
        scores: dict[int, dict[str, object]] = {}
        for answer, sent_at in answer_rows:
            item = scores.setdefault(
                answer.user_tg_id,
                {
                    "user_id": answer.user_tg_id,
                    "username": answer.username,
                    "name": answer.full_name,
                    "correct": 0,
                    "answered": 0,
                    "seconds": 0,
                },
            )
            item["answered"] = int(item["answered"]) + 1
            if answer.is_correct:
                item["correct"] = int(item["correct"]) + 1
            sent = normalized_utc(sent_at)
            answered = normalized_utc(answer.answered_at)
            if sent and answered:
                item["seconds"] = int(item["seconds"]) + max(0, int((answered - sent).total_seconds()))
        leaderboard = sorted(
            scores.values(),
            key=lambda row: (-int(row["correct"]), int(row["seconds"]), -int(row["answered"]), str(row["name"] or "")),
        )
        chat_id = session.chat_id
        title = session.test_name_snapshot
        total_questions = session.total_questions
        stop_vote_message_id = session.stop_vote_message_id
        last_poll_message_id = last_poll.message_id if last_poll else None

    if stop_vote_message_id:
        try:
            await bot_obj.edit_message_text(
                "🛑 <b>Quiz to'xtatildi.</b> Natijalar hisoblandi." if stopped_early else "✅ <b>Quiz tabiiy yakunlandi.</b>",
                chat_id=chat_id,
                message_id=stop_vote_message_id,
            )
        except Exception:  # noqa: BLE001
            pass

    if last_poll_message_id:
        try:
            await bot_obj.stop_poll(chat_id, last_poll_message_id)
        except Exception:  # noqa: BLE001
            pass

    try:
        if not leaderboard:
            await bot_obj.send_message(chat_id, f"🏁 <b>{html.escape(title)}</b> yakunlandi.\n\nHech kim javob bermadi.")
        else:
            lines = [
                "🏁✨ <b>QUIZ YAKUNLANDI!</b> ✨🏁",
                f"📘 <b>{html.escape(title)}</b>",
                "🛑 Test ovoz bilan to'xtatildi." if stopped_early else "✅ Barcha savollar yakunlandi.",
                "",
                "🏆 <b>YAKUNIY LEADERBOARD</b> 🏆",
                "📝 ishlangan/jami · ✅ to'g'ri · ⏱ vaqt",
                "",
            ]
            medals = ["🥇", "🥈", "🥉"]
            for index, row in enumerate(leaderboard[:25], start=1):
                medal = medals[index - 1] if index <= 3 else f"{index}."
                seconds_total = int(row["seconds"])
                time_text = f"{seconds_total // 60}:{seconds_total % 60:02d}"
                username = row["username"] if isinstance(row["username"], str) else None
                full_name = row["name"] if isinstance(row["name"], str) else None
                person = profile_link(int(row["user_id"]), username, full_name, max_length=16)
                lines.append(
                    f"{medal} {person} · 📝{row['answered']}/{total_questions} · "
                    f"✅{row['correct']} · ⏱{time_text}"
                )
            if len(leaderboard) > 25:
                lines.append(f"\n👥 Yana {len(leaderboard) - 25} ta ishtirokchi")
            await bot_obj.send_message(
                chat_id,
                "\n".join(lines),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
    finally:
        with SessionLocal() as db:
            db.execute(delete(GroupQuizSession).where(GroupQuizSession.id == session_id))
            db.commit()


async def publish_bot_commands(bot_obj: Bot) -> None:
    try:
        await bot_obj.set_my_commands(
            [
                BotCommand(command="start", description="Botni boshlash"),
                BotCommand(command="quiz", description="Guruhda test boshlash"),
                BotCommand(command="group_id", description="Guruh ID raqamini ko'rsatish"),
                BotCommand(command="quiz_stop", description="Guruh quizini to'xtatish"),
                BotCommand(command="help", description="Yordam"),
            ]
        )
    except Exception:  # noqa: BLE001
        logger.exception("Telegram bot komandalarini sozlab bo'lmadi")


@router.message(Command("cancel"), F.chat.type == "private")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await set_user_menu_button(message)
    await message.answer("Amal bekor qilindi.", reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id))


@router.message(Command("help"), F.chat.type == "private")
async def help_command(message: Message) -> None:
    await set_user_menu_button(message)
    await message.answer(
        "<b>Yo'riqnoma</b>\n\n"
        "📝 Testlarni boshlash — Mini App'ni ochadi.\n"
        "⚠️ Xatolik haqida xabar — screenshot yoki izohni adminga yuboradi.\n"
        "/cancel — joriy amalni bekor qiladi.",
        reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id),
    )


@router.my_chat_member()
async def bot_group_membership(event: ChatMemberUpdated) -> None:
    if event.chat.type not in GROUP_CHAT_TYPES:
        return
    status = event.new_chat_member.status
    if status not in {"member", "administrator"}:
        return
    group = active_group_or_none(event.chat.id, event.chat.title)
    try:
        if group:
            await event.bot.send_message(
                event.chat.id,
                "Bot guruh quiz uchun ulandi.\n"
                f"Test boshlash: /quiz@{resolved_bot_username}\n"
                f"Testni to'xtatish: /quiz_stop@{resolved_bot_username}\n"
                f"Guruh ID: /group_id@{resolved_bot_username}",
            )
        else:
            await event.bot.send_message(
                event.chat.id,
                "Bot guruhga qo'shildi, lekin bu guruhga hali ruxsat berilmagan.\n"
                f"Admin paneldagi <b>Guruhlar</b> bo'limiga shu ID ni qo'shing:\n<code>{event.chat.id}</code>",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Guruhga ulanish xabari yuborilmadi")


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    user = get_user_by_tg(message.from_user.id)
    if user:
        touch_user(message.from_user.id)
        await set_user_menu_button(message)
        await state.clear()
        await message.answer(
            f"Assalomu alaykum, <b>{html.escape(user.full_name)}</b>! Test botiga xush kelibsiz.",
            reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id),
        )
        return
    await state.set_state(Registration.waiting_name)
    await message.answer(
        "Assalomu alaykum! 👋\n\nTest botiga xush kelibsiz. Iltimos, ism-familiyangizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Registration.waiting_name, F.text, F.chat.type == "private")
async def registration_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Ism-familiya kamida 3 ta belgidan iborat bo'lishi kerak.")
        return
    await state.update_data(full_name=name)
    await state.set_state(Registration.waiting_phone)
    await message.answer(
        f"Rahmat, <b>{html.escape(name)}</b>! Endi telefon raqamingizni yuboring 📱",
        reply_markup=phone_keyboard(),
    )


@router.message(Registration.waiting_phone, F.chat.type == "private")
async def registration_phone(message: Message, state: FSMContext) -> None:
    phone: str | None = None
    if message.contact:
        if message.contact.user_id != message.from_user.id:
            await message.answer("Faqat o'zingizga tegishli kontaktni yuboring.")
            return
        phone = message.contact.phone_number
    elif message.text and re.fullmatch(r"\+?\d{9,15}", message.text.replace(" ", "")):
        phone = message.text.replace(" ", "")
    if not phone:
        await message.answer("Telefon raqamni tugma orqali yuboring yoki +998901234567 ko'rinishida kiriting.")
        return

    data = await state.get_data()
    with SessionLocal() as db:
        user = User(
            telegram_id=message.from_user.id,
            full_name=data["full_name"],
            phone=phone if phone.startswith("+") else f"+{phone}",
            username=message.from_user.username,
            last_active_at=utcnow(),
        )
        db.add(user)
        db.commit()
    await state.clear()
    await set_user_menu_button(message)
    await message.answer(
        "✅ Ro'yxatdan muvaffaqiyatli o'tdingiz!",
        reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id),
    )


@router.message(button_is("Statistika"), F.chat.type == "private")
async def personal_stats(message: Message) -> None:
    await set_user_menu_button(message)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if not user:
            await message.answer("Avval /start orqali ro'yxatdan o'ting.")
            return
        await message.answer(
            "📊 <b>Statistika</b>\n\n"
            "Shaxsiy test tarixi bazada saqlanmaydi. Har bir test yakunida natijangiz ekranda ko'rsatiladi.\n\n"
            "Umumiy anonim statistika faqat admin panel uchun yuritiladi."
        )


@router.message(button_is("Xatolik haqida xabar"), F.chat.type == "private")
async def report_begin(message: Message, state: FSMContext) -> None:
    await set_user_menu_button(message)
    user = get_user_by_tg(message.from_user.id)
    if not user:
        await message.answer("Avval /start orqali ro'yxatdan o'ting.")
        return
    await state.set_state(ReportState.waiting_report)
    await message.answer(
        "⚠️ <b>Xatolik haqida xabar berish</b>\n\n"
        "Testdagi xatolik screenshotini va mazmunini bitta xabarda yuboring. "
        "Faqat matn yuborish ham mumkin.\n\nBekor qilish: /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ReportState.waiting_report, F.chat.type == "private")
async def report_receive(message: Message, state: FSMContext) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if not user:
            await state.clear()
            await message.answer("Foydalanuvchi topilmadi. /start ni bosing.")
            return
        text = message.caption or message.text or "Media fayl yuborildi"
        report = ErrorReport(user_id=user.id, message_text=text, telegram_msg_id=message.message_id)
        db.add(report)
        db.commit()
        db.refresh(report)

        admin_msg_ids: list[int] = []
        for admin_id in settings.admin_id_set:
            try:
                header = await message.bot.send_message(
                    admin_id,
                    "🚨 <b>XATOLIK ANIQLANDI!</b>\n\n"
                    f"👤 Foydalanuvchi: {html.escape(user.full_name)}\n"
                    f"🆔 ID: <code>{user.telegram_id}</code>\n"
                    f"📱 Tel: {html.escape(user.phone)}\n"
                    f"🕐 Vaqt: {datetime.now(TASHKENT).strftime('%d.%m.%Y %H:%M')}",
                )
                copied = await message.bot.copy_message(
                    chat_id=admin_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                    reply_to_message_id=header.message_id,
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="✅ Xatolik to'g'irlandi", callback_data=f"fix_report:{report.id}")]]
                    ),
                )
                admin_msg_ids.append(copied.message_id)
            except Exception:  # noqa: BLE001
                logger.exception("Xatolik xabarini adminga yuborib bo'lmadi")
        report.admin_msg_ids = admin_msg_ids
        db.commit()

    await state.clear()
    await message.answer(
        "✅ Xabaringiz adminga yuborildi. Xatolik to'g'irlangach sizga xabar beramiz!",
        reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id),
    )


@router.callback_query(F.data.startswith("fix_report:"))
async def fix_report(callback: CallbackQuery) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    report_id = int(callback.data.split(":", 1)[1])
    with SessionLocal() as db:
        report = db.scalar(select(ErrorReport).where(ErrorReport.id == report_id))
        if not report:
            await callback.answer("Xabar topilmadi", show_alert=True)
            return
        if report.status == "fixed":
            await callback.answer("Bu xatolik avval to'g'irlangan")
            return
        report.status = "fixed"
        report.fixed_at = utcnow()
        report.fixed_by = callback.from_user.id
        user = db.scalar(select(User).where(User.id == report.user_id))
        db.commit()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=f"✅ To'g'irlandi ({datetime.now(TASHKENT).strftime('%d.%m.%Y')})", callback_data="already_fixed")]]
            )
        )
    if user:
        try:
            await callback.bot.send_message(user.telegram_id, "✅ Siz yuborgan xatolik to'g'irlandi!\nE'tiboringiz uchun rahmat! 🙏")
        except TelegramForbiddenError:
            pass
    await callback.answer("Xatolik to'g'irlangan deb belgilandi")


@router.callback_query(F.data == "already_fixed")
async def already_fixed(callback: CallbackQuery) -> None:
    await callback.answer("Bu xatolik to'g'irlangan")


@router.message(button_is("Admin bilan aloqa"), F.chat.type == "private")
async def contact_admin(message: Message) -> None:
    await set_user_menu_button(message)
    username = settings.admin_username.lstrip("@")
    if not username:
        await message.answer("Admin username hali sozlanmagan.")
        return
    await message.answer(
        "👨‍💻 <b>Admin bilan bog'lanish</b>\n\nSavol va takliflaringiz bo'lsa, adminga yozishingiz mumkin:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✍️ Adminga yozish", url=f"https://t.me/{username}")]]
        ),
    )


@router.message(button_is("Batafsil statistika"), F.chat.type == "private")
async def detailed_stats(message: Message) -> None:
    await set_user_menu_button(message)
    if not is_admin(message.from_user.id):
        return
    with SessionLocal() as db:
        stats = admin_dashboard_stats(db)
    popular = stats["popular_test"]
    popular_text = f"{html.escape(popular['name'])} ({popular['count']} marta)" if popular else "—"
    await message.answer(
        "📈 <b>BATAFSIL STATISTIKA</b>\n\n"
        "👥 <b>FOYDALANUVCHILAR</b>\n"
        f"├ Bugun: {stats['users']['today']} ta\n"
        f"├ Shu hafta: {stats['users']['week']} ta\n"
        f"└ Jami: {stats['users']['total']} ta\n\n"
        "📝 <b>TESTLAR</b>\n"
        f"├ Bugun ishlangan: {stats['attempts']['today']} ta\n"
        f"├ Jami ishlangan: {stats['attempts']['total']} ta\n"
        f"├ O'rtacha natija: {stats['attempts']['average']}%\n"
        f"└ Eng ko'p: {popular_text}\n\n"
        "⚠️ <b>XATOLIKLAR</b>\n"
        f"├ Ochiq: {stats['reports']['open']} ta\n"
        f"└ To'g'irlangan: {stats['reports']['fixed']} ta\n\n"
        f"🕐 {datetime.now(TASHKENT).strftime('%d.%m.%Y %H:%M')}"
    )


@router.message(button_is("Barchaga xabar"), F.chat.type == "private")
async def broadcast_begin(message: Message, state: FSMContext) -> None:
    await set_user_menu_button(message)
    if not is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_message)
    await message.answer("📢 Yubormoqchi bo'lgan xabaringizni yuboring. Matn, rasm, video yoki fayl mumkin.\nBekor qilish: /cancel")


@router.message(BroadcastState.waiting_message, F.chat.type == "private")
async def broadcast_preview(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
    await state.set_state(BroadcastState.waiting_confirmation)
    await message.answer("Quyidagi xabar yuborilsinmi?")
    await message.bot.copy_message(chat_id=message.chat.id, from_chat_id=message.chat.id, message_id=message.message_id)
    await message.answer(
        "Tasdiqlang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Yuborish", callback_data="broadcast_send"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data="broadcast_cancel"),
            ]]
        ),
    )


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Bekor qilindi")
    if callback.message:
        await callback.message.edit_text("❌ Xabar yuborish bekor qilindi.")


@router.callback_query(F.data == "broadcast_send")
async def broadcast_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    data = await state.get_data()
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    if not source_chat_id or not source_message_id:
        await callback.answer("Xabar topilmadi", show_alert=True)
        return
    await callback.answer("Yuborish boshlandi")
    progress = await callback.message.edit_text("📢 Yuborilmoqda...") if callback.message else None

    with SessionLocal() as db:
        telegram_ids = list(db.scalars(select(User.telegram_id).where(User.is_blocked.is_(False))))
    sent = 0
    failed = 0
    for index, telegram_id in enumerate(telegram_ids, start=1):
        try:
            await callback.bot.copy_message(chat_id=telegram_id, from_chat_id=source_chat_id, message_id=source_message_id)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await callback.bot.copy_message(chat_id=telegram_id, from_chat_id=source_chat_id, message_id=source_message_id)
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except TelegramForbiddenError:
            failed += 1
            with SessionLocal() as db:
                user = db.scalar(select(User).where(User.telegram_id == telegram_id))
                if user:
                    user.is_blocked = True
                    db.commit()
        except Exception:  # noqa: BLE001
            failed += 1
        if index % 25 == 0:
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(0.05)
        if progress and index % 100 == 0:
            await progress.edit_text(f"📢 Yuborilmoqda... {index}/{len(telegram_ids)}")

    with SessionLocal() as db:
        db.add(Broadcast(admin_tg_id=callback.from_user.id, content_type="copy_message", sent_count=sent, failed_count=failed))
        db.commit()
    await state.clear()
    if progress:
        await progress.edit_text(f"✅ Yuborildi: {sent} ta\n❌ Yetib bormadi: {failed} ta")


@router.message(addressed_group_command_is("quiz", "testlar", "guruh_test"))
async def group_quiz_menu(message: Message) -> None:
    if not is_group_chat(message):
        await message.answer("Bu buyruq faqat Telegram guruhlarida ishlaydi.")
        return
    group = active_group_or_none(message.chat.id, message.chat.title)
    if not group:
        await message.answer(
            "Bu guruhda test o'tkazishga ruxsat berilmagan.\n"
            f"Admin paneldagi <b>Guruhlar</b> bo'limiga shu ID ni qo'shing:\n<code>{message.chat.id}</code>"
        )
        return
    admin_requested = await user_can_manage_group_quiz(message, message.from_user.id if message.from_user else None)
    force_start_session_id: int | None = None
    force_vote_message_id: int | None = None
    with SessionLocal() as db:
        active_session = db.scalar(
            select(GroupQuizSession)
            .where(GroupQuizSession.chat_id == message.chat.id, GroupQuizSession.status.in_(GROUP_QUIZ_LIVE_STATUSES))
            .order_by(GroupQuizSession.id.desc())
            .with_for_update()
            .limit(1)
        )
        if active_session:
            if active_session.status == "pending_start" and admin_requested:
                active_session.status = "starting"
                force_start_session_id = active_session.id
                force_vote_message_id = active_session.start_vote_message_id
                db.commit()
            else:
                status_text = "boshlash uchun ovoz kutilmoqda" if active_session.status == "pending_start" else "quiz davom etmoqda"
                await message.answer(f"Bu guruhda hozir {status_text}. Bir vaqtda faqat bitta test ishlaydi.")
                return
        if force_start_session_id is None:
            tests = list(
                db.scalars(
                    select(Test)
                    .options(selectinload(Test.rules))
                    .where(Test.is_active.is_(True), Test.test_mode == "exam")
                    .order_by(Test.created_at.desc())
                )
            )
    if force_start_session_id is not None:
        if force_vote_message_id:
            try:
                await message.bot.edit_message_text(
                    "👑 <b>Guruh admini testni darhol boshladi.</b>\n\nOvoz kutish bekor qilindi.",
                    chat_id=message.chat.id,
                    message_id=force_vote_message_id,
                )
            except Exception:  # noqa: BLE001
                pass
        await message.answer("👑 Guruh admini ovoz kutayotgan testni darhol boshladi.")
        await prepare_group_quiz_session(message.bot, force_start_session_id)
        return
    if not tests:
        await message.answer("Hozircha aktiv oddiy test mavjud emas.")
        return
    await message.answer("Guruhda o'tkaziladigan testni tanlang:", reply_markup=group_quiz_test_keyboard(tests))


@router.message(addressed_group_command_is("group_id", "id"))
async def group_id_command(message: Message) -> None:
    if not is_group_chat(message):
        await message.answer("Bu komanda guruh ID sini olish uchun guruh ichida ishlatiladi.")
        return
    await message.answer(
        f"<b>Guruh ID:</b> <code>{message.chat.id}</code>\n"
        "Admin paneldagi <b>Guruhlar</b> bo'limiga aynan shu ID ni kiriting."
    )


@router.callback_query(F.data.startswith("gquiz:start:"))
async def group_quiz_start(callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user or not is_group_chat(callback.message):
        await callback.answer("Bu amal faqat guruhda ishlaydi", show_alert=True)
        return
    group = active_group_or_none(callback.message.chat.id, callback.message.chat.title)
    if not group:
        await callback.answer("Bu guruhga ruxsat berilmagan", show_alert=True)
        return
    starts_immediately = await user_can_manage_group_quiz(callback.message, callback.from_user.id)
    try:
        test_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Test ID noto'g'ri", show_alert=True)
        return

    with SessionLocal() as db:
        db.scalar(select(TelegramGroup).where(TelegramGroup.id == group.id).with_for_update())
        active_session = live_group_quiz_session(db, callback.message.chat.id)
        if active_session:
            await callback.answer("Bu guruhda boshqa test jarayoni bor", show_alert=True)
            return
        test = load_group_quiz_test(db, test_id)
        if not test:
            await callback.answer("Test topilmadi yoki aktiv emas", show_alert=True)
            return
        status = "starting" if starts_immediately else "pending_start"
        deadline = None if starts_immediately else utcnow() + timedelta(seconds=test.group_start_vote_seconds)
        session = GroupQuizSession(
            group_id=group.id,
            test_id=test.id,
            chat_id=callback.message.chat.id,
            test_name_snapshot=test.name,
            status=status,
            question_seconds=test.group_question_seconds,
            start_vote_required=test.group_start_vote_count,
            start_vote_seconds=test.group_start_vote_seconds,
            start_vote_deadline=deadline,
            stop_vote_required=test.group_stop_vote_count,
            stop_vote_seconds=test.group_stop_vote_seconds,
            started_by_tg_id=callback.from_user.id,
        )
        db.add(session)
        db.flush()
        db.commit()
        session_id = session.id
        title = test.name
        required = session.start_vote_required
        vote_seconds = session.start_vote_seconds

    await callback.answer("Test tanlandi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001
        pass
    if starts_immediately:
        await callback.message.answer("👑 Guruh admini testni darhol boshladi.")
        await prepare_group_quiz_session(callback.bot, session_id)
        return

    vote_message = await callback.message.answer(
        "🗳 <b>QUIZNI BOSHLASH UCHUN OVOZ BERING!</b>\n\n"
        f"📘 Test: <b>{html.escape(title)}</b>\n"
        f"👥 Kerakli ovoz: <b>{required} ta</b>\n"
        f"⏳ Vaqt: <b>{format_vote_window(vote_seconds)}</b>\n\n"
        "Yetarli ovoz yig'ilsa test avtomatik boshlanadi.",
        reply_markup=group_vote_keyboard(session_id, "start", 0, required),
    )
    with SessionLocal() as db:
        pending = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id, GroupQuizSession.status == "pending_start"))
        if pending:
            pending.start_vote_message_id = vote_message.message_id
            db.commit()
    asyncio.create_task(expire_start_vote(callback.bot, session_id, vote_seconds))


@router.callback_query(F.data.startswith("gquiz:vote_start:"))
async def group_quiz_start_vote(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message or not is_group_chat(callback.message):
        await callback.answer("Bu ovoz guruh uchun", show_alert=True)
        return
    try:
        session_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Ovoz ma'lumoti noto'g'ri", show_alert=True)
        return
    should_start = False
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id).with_for_update())
        deadline = normalized_utc(session.start_vote_deadline) if session else None
        if not session or session.chat_id != callback.message.chat.id or session.status != "pending_start" or not deadline or deadline <= utcnow():
            await callback.answer("Ovoz berish vaqti tugagan", show_alert=True)
            return
        try:
            db.add(
                GroupQuizVote(
                    session_id=session.id,
                    vote_type="start",
                    user_tg_id=callback.from_user.id,
                    username=callback.from_user.username,
                    full_name=callback.from_user.full_name,
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback()
            await callback.answer("Siz avval ovoz bergansiz")
            return
        count = db.scalar(select(func.count(GroupQuizVote.id)).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "start")) or 0
        required = session.start_vote_required
        if count >= required:
            session.status = "starting"
            should_start = True
        db.commit()
    if should_start:
        await callback.answer("Yetarli ovoz yig'ildi!")
        try:
            await callback.message.edit_text("✅ <b>Yetarli ovoz yig'ildi!</b>\n\nQuiz hozir boshlanadi... 🚀")
        except Exception:  # noqa: BLE001
            pass
        await prepare_group_quiz_session(callback.bot, session_id)
    else:
        await callback.answer(f"Ovozingiz qabul qilindi: {count}/{required}")
        try:
            await callback.message.edit_reply_markup(reply_markup=group_vote_keyboard(session_id, "start", count, required))
        except Exception:  # noqa: BLE001
            pass


@router.message(addressed_group_command_is("quiz_stop", "quiz_cancel", "stop_quiz"))
async def group_quiz_stop(message: Message) -> None:
    if not is_group_chat(message):
        return
    if not active_group_or_none(message.chat.id, message.chat.title):
        return
    stops_immediately = await user_can_manage_group_quiz(message, message.from_user.id if message.from_user else None)
    with SessionLocal() as db:
        session = db.scalar(
            select(GroupQuizSession)
            .where(GroupQuizSession.chat_id == message.chat.id, GroupQuizSession.status.in_(GROUP_QUIZ_LIVE_STATUSES))
            .order_by(GroupQuizSession.id.desc())
            .with_for_update()
            .limit(1)
        )
        if not session:
            await message.answer("Bu guruhda aktiv quiz yo'q.")
            return
        if session.status == "pending_start":
            if not stops_immediately:
                await message.answer("Quiz hali boshlanmagan; boshlash ovozi yakunlanishini kuting.")
                return
            vote_message_id = session.start_vote_message_id
            db.delete(session)
            db.commit()
            if vote_message_id:
                try:
                    await message.bot.edit_message_text(
                        "👑 <b>Admin boshlash ovozini bekor qildi.</b>",
                        chat_id=message.chat.id,
                        message_id=vote_message_id,
                    )
                except Exception:  # noqa: BLE001
                    pass
            await message.answer("👑 Admin boshlash ovozini bekor qildi.")
            return
        if session.status != "active":
            await message.answer("Quiz hozir yakunlanmoqda.")
            return
        session_id = session.id
        if stops_immediately:
            session.status = "stopping"
            session.finished_at = utcnow()
            db.commit()
        else:
            current_deadline = normalized_utc(session.stop_vote_deadline)
            if current_deadline and current_deadline > utcnow():
                await message.answer("To'xtatish uchun ovoz berish allaqachon ochilgan. Yuqoridagi tugmani bosing.")
                return
            db.execute(delete(GroupQuizVote).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "stop"))
            session.stop_vote_deadline = utcnow() + timedelta(seconds=session.stop_vote_seconds)
            session.stop_vote_message_id = None
            required = session.stop_vote_required
            vote_seconds = session.stop_vote_seconds
            title = session.test_name_snapshot
            db.commit()

    if stops_immediately:
        await message.answer("👑 Guruh admini quizni darhol to'xtatdi.")
        await send_group_quiz_results(message.bot, session_id, stopped_early=True)
        return

    vote_message = await message.answer(
        "🛑 <b>QUIZNI TO'XTATISH UCHUN OVOZ</b>\n\n"
        f"📘 Test: <b>{html.escape(title)}</b>\n"
        f"👥 Kerakli ovoz: <b>{required} ta</b>\n"
        f"⏳ Vaqt: <b>{format_vote_window(vote_seconds)}</b>\n\n"
        "Yetarli ovoz yig'ilmasa quiz davom etadi.",
        reply_markup=group_vote_keyboard(session_id, "stop", 0, required),
    )
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id, GroupQuizSession.status == "active"))
        if session:
            session.stop_vote_message_id = vote_message.message_id
            db.commit()
    asyncio.create_task(expire_stop_vote(message.bot, session_id, vote_seconds))


@router.callback_query(F.data.startswith("gquiz:vote_stop:"))
async def group_quiz_stop_vote(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message or not is_group_chat(callback.message):
        await callback.answer("Bu ovoz guruh uchun", show_alert=True)
        return
    try:
        session_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Ovoz ma'lumoti noto'g'ri", show_alert=True)
        return
    should_stop = False
    with SessionLocal() as db:
        session = db.scalar(select(GroupQuizSession).where(GroupQuizSession.id == session_id).with_for_update())
        deadline = normalized_utc(session.stop_vote_deadline) if session else None
        if not session or session.chat_id != callback.message.chat.id or session.status != "active" or not deadline or deadline <= utcnow():
            await callback.answer("Ovoz berish vaqti tugagan", show_alert=True)
            return
        try:
            db.add(
                GroupQuizVote(
                    session_id=session.id,
                    vote_type="stop",
                    user_tg_id=callback.from_user.id,
                    username=callback.from_user.username,
                    full_name=callback.from_user.full_name,
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback()
            await callback.answer("Siz avval ovoz bergansiz")
            return
        count = db.scalar(select(func.count(GroupQuizVote.id)).where(GroupQuizVote.session_id == session.id, GroupQuizVote.vote_type == "stop")) or 0
        required = session.stop_vote_required
        if count >= required:
            session.status = "stopping"
            session.finished_at = utcnow()
            should_stop = True
        db.commit()
    if should_stop:
        await callback.answer("Quiz to'xtatiladi")
        try:
            await callback.message.edit_text("🛑 <b>Yetarli ovoz yig'ildi.</b>\n\nQuiz to'xtatildi, natijalar hisoblanmoqda... 🏆")
        except Exception:  # noqa: BLE001
            pass
        await send_group_quiz_results(callback.bot, session_id, stopped_early=True)
    else:
        await callback.answer(f"Ovozingiz qabul qilindi: {count}/{required}")
        try:
            await callback.message.edit_reply_markup(reply_markup=group_vote_keyboard(session_id, "stop", count, required))
        except Exception:  # noqa: BLE001
            pass


@router.poll_answer()
async def group_quiz_poll_answer(poll_answer: PollAnswer) -> None:
    if not poll_answer.option_ids:
        return
    selected_option = int(poll_answer.option_ids[0])
    user = poll_answer.user
    with SessionLocal() as db:
        quiz_question = db.scalar(
            select(GroupQuizQuestion)
            .options(selectinload(GroupQuizQuestion.session))
            .where(GroupQuizQuestion.poll_id == poll_answer.poll_id)
        )
        if not quiz_question or quiz_question.session.status != "active":
            return
        existing = db.scalar(
            select(GroupQuizAnswer).where(
                GroupQuizAnswer.session_id == quiz_question.session_id,
                GroupQuizAnswer.quiz_question_id == quiz_question.id,
                GroupQuizAnswer.user_tg_id == user.id,
            )
        )
        is_correct_answer = selected_option == quiz_question.correct_option_id
        if existing:
            existing.option_id = selected_option
            existing.is_correct = is_correct_answer
            existing.answered_at = utcnow()
            existing.username = user.username
            existing.full_name = user.full_name
        else:
            db.add(
                GroupQuizAnswer(
                    session_id=quiz_question.session_id,
                    quiz_question_id=quiz_question.id,
                    user_tg_id=user.id,
                    username=user.username,
                    full_name=user.full_name,
                    option_id=selected_option,
                    is_correct=is_correct_answer,
                )
            )
        db.commit()


@router.message(F.text)
async def unknown_text(message: Message) -> None:
    if is_group_chat(message):
        return
    await set_user_menu_button(message)
    if message.from_user and get_user_by_tg(message.from_user.id):
        await message.answer("Tugmalardan birini tanlang.", reply_markup=main_menu(is_admin(message.from_user.id), message.from_user.id))
        return
    await message.answer("Avval /start ni bosing va ro'yxatdan o'ting.")


async def setup_bot() -> None:
    global bot, dp, resolved_bot_username
    if not settings.bot_token:
        logger.warning("BOT_TOKEN mavjud emas, Telegram bot ishga tushmaydi")
        return
    with SessionLocal() as db:
        stale_count = db.scalar(select(func.count(GroupQuizSession.id))) or 0
        if stale_count:
            db.execute(delete(GroupQuizSession))
            db.commit()
            logger.info("%s ta eski guruh quiz jarayoni tozalandi", stale_count)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        identity = await bot.get_me()
        if identity.username:
            resolved_bot_username = identity.username.casefold()
    except Exception:  # noqa: BLE001
        logger.exception("Bot username aniqlanmadi, BOT_USERNAME qiymati ishlatiladi")
    if dp is None:
        dp = Dispatcher()
        dp.include_router(router)
    await publish_bot_commands(bot)

    if settings.normalized_webapp_url.startswith("https://") and settings.webhook_secret:
        webhook_url = f"{settings.normalized_webapp_url}/api/telegram/webhook"
        await bot.set_webhook(
            webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=TELEGRAM_ALLOWED_UPDATES,
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook o'rnatildi: %s", webhook_url)
    else:
        logger.warning("HTTPS WEBAPP_URL yoki WEBHOOK_SECRET yo'q. Webhook o'rnatilmadi")


async def shutdown_bot() -> None:
    global bot
    if bot:
        await bot.session.close()
        bot = None


async def process_update(update_data: dict) -> None:
    if not bot or not dp:
        raise RuntimeError("Bot sozlanmagan")
    update = Update.model_validate(update_data, context={"bot": bot})
    await dp.feed_update(bot, update)

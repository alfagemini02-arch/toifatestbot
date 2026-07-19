from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal
from .models import Broadcast, ErrorReport, User, utcnow
from .services import admin_dashboard_stats, user_stats

logger = logging.getLogger(__name__)
settings = get_settings()
TASHKENT = ZoneInfo("Asia/Tashkent")

bot: Bot | None = None
dp: Dispatcher | None = None
router = Router()


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


def main_menu(admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📝 Testlarni boshlash", web_app=WebAppInfo(url=f"{settings.normalized_webapp_url}/app"))],
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="⚠️ Xatolik haqida xabar")],
        [KeyboardButton(text="👨‍💻 Admin bilan aloqa")],
    ]
    if admin:
        rows.append([KeyboardButton(text="📈 Batafsil statistika"), KeyboardButton(text="📢 Barchaga xabar")])
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


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Amal bekor qilindi.", reply_markup=main_menu(is_admin(message.from_user.id)))


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "<b>Yo'riqnoma</b>\n\n"
        "📝 Testlarni boshlash — Mini App'ni ochadi.\n"
        "📊 Statistika — shaxsiy natijalaringiz.\n"
        "⚠️ Xatolik haqida xabar — screenshot yoki izohni adminga yuboradi.\n"
        "/cancel — joriy amalni bekor qiladi.",
        reply_markup=main_menu(is_admin(message.from_user.id)),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    user = get_user_by_tg(message.from_user.id)
    if user:
        touch_user(message.from_user.id)
        await state.clear()
        await message.answer(
            f"Assalomu alaykum, <b>{html.escape(user.full_name)}</b>! Test botiga xush kelibsiz.",
            reply_markup=main_menu(is_admin(message.from_user.id)),
        )
        return
    await state.set_state(Registration.waiting_name)
    await message.answer(
        "Assalomu alaykum! 👋\n\nTest botiga xush kelibsiz. Iltimos, ism-familiyangizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Registration.waiting_name, F.text)
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


@router.message(Registration.waiting_phone)
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
    await message.answer(
        "✅ Ro'yxatdan muvaffaqiyatli o'tdingiz!",
        reply_markup=main_menu(is_admin(message.from_user.id)),
    )


@router.message(F.text == "📊 Statistika")
async def personal_stats(message: Message) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if not user:
            await message.answer("Avval /start orqali ro'yxatdan o'ting.")
            return
        stats = user_stats(db, user)
        registered = user.registered_at.astimezone(TASHKENT).strftime("%d.%m.%Y")
        best_line = f"{stats['best_percentage']}%"
        if stats["best_test"]:
            best_line += f" ({html.escape(stats['best_test'])})"
        await message.answer(
            "📊 <b>Sizning statistikangiz</b>\n\n"
            f"👤 Ism: {html.escape(user.full_name)}\n"
            f"📅 Ro'yxatdan o'tgan sana: {registered}\n\n"
            f"✅ Ishlangan testlar: {stats['count']} ta\n"
            f"🎯 O'rtacha natija: {stats['average']}%\n"
            f"🏆 Eng yaxshi natija: {best_line}\n"
            f"📆 Bugun ishlangan: {stats['today']} ta"
        )


@router.message(F.text == "⚠️ Xatolik haqida xabar")
async def report_begin(message: Message, state: FSMContext) -> None:
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


@router.message(ReportState.waiting_report)
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
        reply_markup=main_menu(is_admin(message.from_user.id)),
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


@router.message(F.text == "👨‍💻 Admin bilan aloqa")
async def contact_admin(message: Message) -> None:
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


@router.message(F.text == "📈 Batafsil statistika")
async def detailed_stats(message: Message) -> None:
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


@router.message(F.text == "📢 Barchaga xabar")
async def broadcast_begin(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_message)
    await message.answer("📢 Yubormoqchi bo'lgan xabaringizni yuboring. Matn, rasm, video yoki fayl mumkin.\nBekor qilish: /cancel")


@router.message(BroadcastState.waiting_message)
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


async def setup_bot() -> None:
    global bot, dp
    if not settings.bot_token:
        logger.warning("BOT_TOKEN mavjud emas, Telegram bot ishga tushmaydi")
        return
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    if dp is None:
        dp = Dispatcher()
        dp.include_router(router)

    if settings.normalized_webapp_url.startswith("https://") and settings.webhook_secret:
        webhook_url = f"{settings.normalized_webapp_url}/api/telegram/webhook"
        await bot.set_webhook(
            webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
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

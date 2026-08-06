from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.i18n import LANGUAGES, button_texts, t

CHECK_BUTTONS = button_texts("button_check")
TERMS_BUTTONS = button_texts("button_terms")
LANGUAGE_BUTTONS = button_texts("button_language")
CANCEL_BUTTONS = button_texts("button_cancel")


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"set_lang:{code}")]
            for code, label in LANGUAGES.items()
        ]
    )


def contact_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "button_contact"), request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=t(lang, "contact_placeholder"),
    )


def terms_keyboard(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "button_accept_terms"), callback_data="accept_terms")],
        ]
    )


def main_menu_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "button_check")), KeyboardButton(text=t(lang, "button_fees"))],
            [KeyboardButton(text=t(lang, "button_terms"))],
            [KeyboardButton(text=t(lang, "button_language"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=t(lang, "menu_placeholder"),
    )


def cancel_keyboard(lang: str = "uz") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "button_cancel"))]],
        resize_keyboard=True,
    )

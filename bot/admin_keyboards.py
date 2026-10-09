from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.emojis import fb

MENU_HOME = "menu:home"
ADMIN_SESSIONS = "admin:sessions"
ADMIN_SESSION_ADD = "admin:session:add"
ADMIN_SESSION_ADD_PHONE = "admin:session:phone"
ADMIN_SESSION_ADD_FILE = "admin:session:file"
ADMIN_SESSION_FILE_DONE = "admin:session:file:done"
ADMIN_SESSION_DEL = "admin:session:del"
ADMIN_SESSION_DEL_PREFIX = "admin:session:rm:"
AUTH_CODE_DIGIT_PREFIX = "auth_code:d:"
AUTH_CODE_BACK = "auth_code:back"
AUTH_CODE_DONE = "auth_code:done"
CANCEL = "cancel"


def admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Аккаунты", callback_data=ADMIN_SESSIONS)],
        ]
    )


def sessions_menu_kb(*, has_sessions: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="➕ Добавить по номеру", callback_data=ADMIN_SESSION_ADD_PHONE)],
        [InlineKeyboardButton(text="📁 Загрузить .session", callback_data=ADMIN_SESSION_ADD_FILE)],
    ]
    if has_sessions:
        rows.append(
            [InlineKeyboardButton(text="🗑 Удалить аккаунт", callback_data=ADMIN_SESSION_DEL)]
        )
    rows.append([InlineKeyboardButton(text=f"{fb('back')} Назад", callback_data=MENU_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_delete_kb(items: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in items:
        label = row["label"]
        username = row.get("username")
        status = "✅" if row.get("alive") else "❌"
        title = f"{status} @{username}" if username else f"{status} {label}"
        if row.get("tg_user_id"):
            title = f"{title} ({row['tg_user_id']})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:64],
                    callback_data=f"{ADMIN_SESSION_DEL_PREFIX}{label}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=f"{fb('back')} Назад", callback_data=ADMIN_SESSIONS)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def auth_code_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(n), callback_data=f"{AUTH_CODE_DIGIT_PREFIX}{n}") for n in (1, 2, 3)],
            [InlineKeyboardButton(text=str(n), callback_data=f"{AUTH_CODE_DIGIT_PREFIX}{n}") for n in (4, 5, 6)],
            [InlineKeyboardButton(text=str(n), callback_data=f"{AUTH_CODE_DIGIT_PREFIX}{n}") for n in (7, 8, 9)],
            [
                InlineKeyboardButton(text="⌫", callback_data=AUTH_CODE_BACK),
                InlineKeyboardButton(text="0", callback_data=f"{AUTH_CODE_DIGIT_PREFIX}0"),
                InlineKeyboardButton(text=f"{fb('done')} OK", callback_data=AUTH_CODE_DONE),
            ],
            [InlineKeyboardButton(text=f"{fb('cancel')} Отмена", callback_data=CANCEL)],
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{fb('cancel')} Отмена", callback_data=CANCEL)],
        ]
    )


def upload_files_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{fb('done')} Готово", callback_data=ADMIN_SESSION_FILE_DONE)],
            [InlineKeyboardButton(text=f"{fb('cancel')} Отмена", callback_data=CANCEL)],
        ]
    )

from __future__ import annotations

import logging

from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    SessionExpiredError,
    SessionRevokedError,
)

logger = logging.getLogger(__name__)

SESSION_FATAL = (
    AuthKeyDuplicatedError,
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    SessionExpiredError,
    SessionRevokedError,
)


def is_session_fatal(exc: BaseException) -> bool:
    return isinstance(exc, SESSION_FATAL)


def format_session_upload_error(exc: BaseException | str) -> str:
    if isinstance(exc, BaseException):
        if isinstance(exc, AuthKeyDuplicatedError):
            return (
                "Сессия использовалась с двух устройств одновременно — "
                "нужен новый вход и новый .session"
            )
        if isinstance(exc, SessionRevokedError):
            return (
                "Сессия отозвана в Telegram (завершены все сессии) — "
                "войдите заново и загрузите новый .session"
            )
        if isinstance(exc, SessionExpiredError):
            return "Сессия истекла — войдите заново и загрузите новый .session"
        if isinstance(exc, (AuthKeyUnregisteredError, AuthKeyInvalidError)):
            return (
                "Сессия недействительна или удалена — "
                "нужен новый .session с авторизованного аккаунта"
            )
        if isinstance(exc, FileNotFoundError):
            return "Файл сессии не найден или повреждён"
        text = str(exc)
    else:
        text = exc

    lower = text.lower()
    if "session not authorized" in lower or "not authorized" in lower:
        return (
            "Сессия мёртвая или недействительна — файл не авторизован. "
            "Войдите в аккаунт заново и загрузите свежий .session"
        )
    if "этот аккаунт уже добавлен" in lower:
        return "Этот аккаунт уже добавлен в бота — повторная загрузка не нужна"
    if "session file not found" in lower:
        return "Файл сессии не найден или повреждён"
    return text[:300]


def log_session_fatal(session_name: str, exc: BaseException) -> None:
    if isinstance(exc, AuthKeyDuplicatedError):
        reason = "сессия использовалась с двух IP одновременно"
    elif isinstance(exc, SessionRevokedError):
        reason = "пользователь завершил все сессии в Telegram"
    elif isinstance(exc, SessionExpiredError):
        reason = "сессия истекла"
    else:
        reason = "сессия отозвана или удалена"
    logger.critical(
        "Юзербот недоступен (%s): %s.\n"
        "→ Остановите все другие процессы с этой сессией (другой терминал, VPS, parser)\n"
        "→ Удалите файлы %s.session и %s.session-journal\n"
        "→ Перезапустите бота и войдите по телефону заново",
        reason,
        exc,
        session_name,
        session_name,
    )

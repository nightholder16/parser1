from __future__ import annotations

import asyncio
import logging
import re
import time

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatJoinRequest, Message

from config import Settings
from storage.database import Database

logger = logging.getLogger(__name__)
router = Router()

_HOUR_RE = re.compile(r"^(\d+):1h$")
_HOUR_WORDS = {"1h", "1ч", "час", "hour", "h"}
HOUR_SECONDS = 3600


def _parse_user_id(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text.isdigit():
        return None
    uid = int(text)
    if uid <= 0:
        return None
    return uid


def _parse_link_name(name: str) -> tuple[int, bool] | None:
    hour = _HOUR_RE.fullmatch(name)
    if hour:
        return int(hour.group(1)), True
    if name.isdigit():
        uid = int(name)
        return (uid, False) if uid > 0 else None
    return None


def _keep_retrying(exc: TelegramBadRequest) -> bool:
    msg = (exc.message or "").lower()
    return any(
        part in msg
        for part in ("not enough rights", "chat_admin_required", "need administrator rights")
    )


@router.message(Command("sutpa"))
async def on_sutpa(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
) -> None:
    if not message.from_user or not settings.is_admin(message.from_user.id):
        return
    args = (command.args or "").split()
    hour = False
    if len(args) == 1:
        target_id = _parse_user_id(args[0])
    elif len(args) == 2 and args[1].lower() in _HOUR_WORDS:
        target_id = _parse_user_id(args[0])
        hour = True
    else:
        target_id = None
    if target_id is None:
        await message.answer(
            "Использование:\n"
            "<code>/sutpa 123456789</code> — постоянно\n"
            "<code>/sutpa 123456789 1h</code> — на час"
        )
        return
    try:
        link = await bot.create_chat_invite_link(
            chat_id=settings.alert_chat_id,
            name=(f"{target_id}:1h" if hour else str(target_id))[:32],
            creates_join_request=True,
        )
    except TelegramBadRequest as exc:
        logger.exception("Failed to create invite link for %s", target_id)
        await message.answer(f"Не удалось создать ссылку: {exc.message}")
        return
    if hour:
        await message.answer(
            f"Ссылка на 1 час для <code>{target_id}</code>:\n{link.invite_link}\n\n"
            "Час считается с момента входа, потом бот исключит из чата."
        )
        return
    await message.answer(f"Ссылка для <code>{target_id}</code>:\n{link.invite_link}")


@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest, settings: Settings, db: Database) -> None:
    if event.chat.id != settings.alert_chat_id:
        return
    parsed = _parse_link_name((event.invite_link.name if event.invite_link else "") or "")
    if parsed is None:
        try:
            await event.decline()
        except TelegramBadRequest:
            logger.exception("Failed to decline join request for %s", event.from_user.id)
        return
    expected, hour = parsed
    ok = expected == event.from_user.id
    try:
        if not ok:
            await event.decline()
            logger.info("Declined join %s (link name %r)", event.from_user.id, event.invite_link.name if event.invite_link else "")
            return
        await event.approve()
        if hour:
            await db.set_temp_access(event.from_user.id, event.chat.id, int(time.time()) + HOUR_SECONDS)
            logger.info("Approved join %s for 1h", event.from_user.id)
        else:
            await db.clear_temp_access(event.from_user.id, event.chat.id)
            logger.info("Approved join %s", event.from_user.id)
    except TelegramBadRequest:
        logger.exception(
            "Failed to %s join request for %s",
            "approve" if ok else "decline",
            event.from_user.id,
        )


async def expire_access_loop(bot: Bot, db: Database) -> None:
    while True:
        try:
            now = int(time.time())
            for user_id, chat_id in await db.list_expired_access(now):
                try:
                    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                except TelegramBadRequest as exc:
                    if _keep_retrying(exc):
                        logger.warning("hour kick %s: %s", user_id, exc.message)
                        continue
                    logger.info("hour kick %s skipped: %s", user_id, exc.message)
                await db.clear_temp_access(user_id, chat_id)
                logger.info("1h access ended for %s", user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("temp access loop")
        await asyncio.sleep(15)

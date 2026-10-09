from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)

CLAIM_CB = "claim:take"
_NFT_URL_RE = re.compile(r"https://t\.me/nft/[A-Za-z0-9_\-]+")

logger = logging.getLogger(__name__)
router = Router()


def claim_kb(
    owner_username: str = "",
    gift_slug: str = "",
    lot_id: str = "",
    bot_username: str = "TheparsTest_bot",
) -> InlineKeyboardMarkup:
    clean_owner = owner_username.lstrip("@") if owner_username else ""
    owner_url = f"https://t.me/{clean_owner}" if clean_owner else "https://t.me"
    gift_url = f"https://t.me/nft/{gift_slug}" if gift_slug else "https://t.me"
    claim_url = f"https://t.me/{bot_username.lstrip('@')}?start=claim_{lot_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 View Owner ↗️",
                    url=owner_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Gift Link ↗️",
                    url=gift_url
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Занять лог",
                    url=claim_url
                )
            ],
        ]
    )

def claimed_text(username: str | None, *, user_id: int, full_name: str | None) -> str:
    if username:
        return f"Занято @{escape(username)}"
    name = (full_name or "").strip() or str(user_id)
    return f"Занято <b>{escape(name)}</b> (<code>{user_id}</code>)"


async def _send_claim_copy(callback: CallbackQuery, original: str) -> bool:
    user = callback.from_user
    if user is None or not original:
        return False
    kwargs: dict = {"chat_id": user.id, "text": original}
    match = _NFT_URL_RE.search(original)
    if match:
        kwargs["link_preview_options"] = LinkPreviewOptions(
            is_disabled=False,
            url=match.group(0),
            prefer_large_media=True,
        )
    try:
        await callback.bot.send_message(**kwargs)
        return True
    except TelegramForbiddenError:
        logger.info("Cannot DM claim copy to %s — bot not started", user.id)
        return False
    except Exception:
        logger.exception("Failed to DM claim copy to %s", user.id)
        return False


@router.callback_query(F.data == CLAIM_CB)
async def on_claim(callback: CallbackQuery) -> None:
    message = callback.message
    user = callback.from_user
    if message is None or user is None:
        await callback.answer()
        return

    original = (message.html_text or message.text or "").strip()
    if original.startswith("Занято") or message.reply_markup is None:
        await callback.answer("Уже занято", show_alert=True)
        return

    text = claimed_text(
        user.username,
        user_id=user.id,
        full_name=user.full_name,
    )
    try:
        await message.edit_text(text, reply_markup=None)
    except Exception:
        await callback.answer("Уже занято", show_alert=True)
        return

    dm_ok = await _send_claim_copy(callback, original)
    if dm_ok:
        await callback.answer("Занято")
        return
    await callback.answer(
        "Занято. Напиши /start этому боту — в следующий раз пришлю копию в личку.",
        show_alert=True,
    )

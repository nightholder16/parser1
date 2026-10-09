from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, Message


async def edit_screen(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    elif message.text is not None:
        await message.edit_text(text, reply_markup=reply_markup)
    elif message.caption is not None:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await message.answer(text, reply_markup=reply_markup)

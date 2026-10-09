from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from bot.handlers import admin_sessions, claim, invite
from bot.middleware import InjectDependenciesMiddleware
from clients.market_pool import MarketSessionPool
from config import Settings
from storage.database import Database

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )


def create_dispatcher(
    settings: Settings,
    db: Database,
    pool: MarketSessionPool,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(
        InjectDependenciesMiddleware(settings=settings, db=db, pool=pool)
    )

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if not settings.is_admin(uid):
            await message.answer("Копии занятых лотов буду присылать сюда.")
            return
        await admin_sessions.show_home(message, db=db, pool=pool, edit=False)

    dp.include_router(invite.router)
    dp.include_router(claim.router)
    dp.include_router(admin_sessions.router)
    return dp

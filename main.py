from __future__ import annotations

import asyncio
import logging
import sys

from bot.app import create_bot, create_dispatcher
from bot.handlers.invite import expire_access_loop
from clients.market_pool import MarketSessionPool
from config import Settings
from monitor.watcher import MarketWatcher
from services.topics import ensure_forum_topics
from storage.database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("telethon").setLevel(logging.ERROR)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logger = logging.getLogger("main")


async def run() -> None:
    settings = Settings.load()
    db = Database(settings.database_path)
    await db.connect()

    bot = create_bot(settings)
    pool = MarketSessionPool(settings)
    loaded = await pool.start()
    if loaded == 0:
        logger.warning("No sessions loaded — add them via bot admin menu")

    topics = await ensure_forum_topics(bot, settings, db)
    logger.info(
        "Forum topics ready: %s band(s) in chat %s",
        len(topics),
        settings.alert_chat_id,
    )

    dp = create_dispatcher(settings, db, pool)
    watcher = MarketWatcher(settings, db, pool, bot, topics)

    logger.info(
        "Bot ready · db=%s · sessions=%s · dir=%s · chat=%s",
        settings.database_path,
        loaded,
        settings.sessions_dir,
        settings.alert_chat_id,
    )

    background_tasks = [
        asyncio.create_task(watcher.run_forever()),
        asyncio.create_task(expire_access_loop(bot, db)),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await pool.shutdown()
        await db.close()
        await bot.session.close()


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())


if __name__ == "__main__":
    main()

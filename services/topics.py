from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from config import Settings
from services.bands import OTHER_BAND_KEY
from storage.database import Database

logger = logging.getLogger(__name__)


async def ensure_forum_topics(bot: Bot, settings: Settings, db: Database) -> dict[str, int]:
    chat_id = settings.alert_chat_id
    existing = await db.list_topics()
    result: dict[str, int] = {}

    planned: list[tuple[str, str]] = [
        (band.key, band.title) for band in settings.price_bands
    ]
    planned.append((OTHER_BAND_KEY, settings.other_topic_title))
    planned_keys = {key for key, _ in planned}
    leftovers = [
        (key, cached)
        for key, cached in existing.items()
        if key not in planned_keys and cached.get("topic_id")
    ]
    leftovers.sort(key=lambda item: int(item[1]["topic_id"]))

    for band_key, title in planned:
        cached = existing.get(band_key)
        if cached and cached.get("topic_id"):
            topic_id = int(cached["topic_id"])
            result[band_key] = topic_id
            old_title = str(cached.get("title") or "")
            if old_title != title:
                try:
                    await bot.edit_forum_topic(
                        chat_id=chat_id, message_thread_id=topic_id, name=title[:128]
                    )
                    await db.upsert_topic(band_key, topic_id, title)
                    logger.info(
                        "Renamed topic: %s → thread %s (%r → %r)",
                        band_key,
                        topic_id,
                        old_title,
                        title,
                    )
                except TelegramBadRequest:
                    logger.exception(
                        "Failed to rename topic %s (thread %s)", band_key, topic_id
                    )
            else:
                logger.info("Topic ready: %s → thread %s (%s)", band_key, topic_id, title)
            continue

        if leftovers:
            old_key, leftover = leftovers.pop(0)
            topic_id = int(leftover["topic_id"])
            old_title = str(leftover.get("title") or "")
            try:
                if old_title != title:
                    await bot.edit_forum_topic(
                        chat_id=chat_id, message_thread_id=topic_id, name=title[:128]
                    )
                await db.rekey_topic(old_key, band_key, title)
                result[band_key] = topic_id
                logger.info(
                    "Reused topic: %s → %s thread %s (%r → %r)",
                    old_key,
                    band_key,
                    topic_id,
                    old_title,
                    title,
                )
                continue
            except TelegramBadRequest:
                logger.exception(
                    "Failed to reuse topic %s (thread %s) as %s",
                    old_key,
                    topic_id,
                    band_key,
                )

        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=title[:128])
            topic_id = int(topic.message_thread_id)
            await db.upsert_topic(band_key, topic_id, title)
            result[band_key] = topic_id
            logger.info("Created topic: %s → thread %s (%s)", band_key, topic_id, title)
        except TelegramBadRequest as exc:
            logger.error(
                "Failed to create topic %r in chat %s: %s. "
                "Бот должен быть админом форум-группы с правом Manage Topics.",
                title,
                chat_id,
                exc,
            )
            raise

    return result

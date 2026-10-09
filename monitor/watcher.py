from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot import formatters as fmt
from bot.handlers.claim import claim_kb
from clients.market_pool import MarketSessionPool
from clients.market_session import MarketSession
from clients.session_errors import is_session_fatal
from config import Settings
from parser.filters import WatchFilter
from parser.market import fetch_gift_types, iter_watch_listing_pages
from parser.serializers import normalize_owner_username
from services.bands import OTHER_BAND_KEY, band_key_for_listing, price_label
from storage.database import Database

logger = logging.getLogger(__name__)

_NOTIFY_MIN_INTERVAL_SEC = 3.1
_NOTIFY_MAX_RETRIES = 4


def _listing_line(row: dict, settings: Settings) -> str:
    title = row.get("title") or "NFT"
    num = row.get("num")
    slug = row.get("slug") or "?"
    num_part = f" #{num:,}" if num is not None else ""
    return f"{title}{num_part} · {price_label(row, settings)} · {slug}"


class MarketWatcher:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        pool: MarketSessionPool,
        bot: Bot,
        topics: dict[str, int],
    ) -> None:
        self.settings = settings
        self.db = db
        self.pool = pool
        self.bot = bot
        self.topics = topics
        self._lock = asyncio.Lock()
        self._cycle = 0
        self._waiting_sessions = False
        self._notify_lock = asyncio.Lock()
        self._last_notify_at = 0.0

    async def run_forever(self) -> None:
        logger.info(
            "Watcher started: poll every %ss, pages=%s, delay=%ss, chat=%s, "
            "topics=%s, sessions=%s",
            self.settings.poll_interval_sec,
            self.settings.resale_max_pages,
            self.settings.resale_request_delay_sec,
            self.settings.alert_chat_id,
            len(self.topics),
            self.pool.total_count(),
        )
        while True:
            try:
                await self.poll_once()
            except Exception:
                logger.exception("Watcher poll failed")
            await asyncio.sleep(self.settings.poll_interval_sec)

    async def poll_once(self) -> None:
        async with self._lock:
            self._cycle += 1
            primary = self.pool.primary()
            if not primary:
                if not self._waiting_sessions:
                    logger.warning("No alive sessions — waiting until you add accounts")
                    self._waiting_sessions = True
                return
            if self._waiting_sessions:
                logger.info("Sessions available again — resuming watcher")
                self._waiting_sessions = False

            client = await primary.ensure()
            if not client:
                logger.warning("[cycle %s] primary session dead, skip", self._cycle)
                return

            try:
                gift_types = await fetch_gift_types(client)
            except Exception as exc:
                if is_session_fatal(exc):
                    await primary.mark_dead(exc)
                raise

            if not gift_types:
                logger.info("[cycle %s] no gifts on market", self._cycle)
                return

            baseline = await self.db.is_baseline_done()
            mode = "baseline" if not baseline else "poll"
            assignments = self.pool.distribute(gift_types)
            if not assignments:
                logger.error("[cycle %s] no session assignments", self._cycle)
                return

            parts = ", ".join(
                f"{sess.label}={len(chunk)}" for sess, chunk in assignments
            )
            logger.info(
                "[cycle %s] %s · %s gift type(s) · %s session(s): %s",
                self._cycle,
                mode,
                len(gift_types),
                len(assignments),
                parts,
            )

            results = await asyncio.gather(
                *[
                    self._poll_session_gifts(sess, chunk, baseline=not baseline)
                    for sess, chunk in assignments
                ],
                return_exceptions=True,
            )

            total_new = 0
            for (sess, chunk), result in zip(assignments, results):
                if isinstance(result, BaseException):
                    logger.exception(
                        "Session [%s] poll failed (%s gifts)",
                        sess.label,
                        len(chunk),
                        exc_info=result,
                    )
                else:
                    total_new += int(result)

            if not baseline:
                await self.db.mark_baseline_done()
                logger.info(
                    "[cycle %s] baseline done, further listings will alert",
                    self._cycle,
                )

            logger.info("[cycle %s] done, %s new listing(s)", self._cycle, total_new)

    async def _poll_session_gifts(
        self,
        session: MarketSession,
        gifts: list[dict],
        *,
        baseline: bool,
    ) -> int:
        total_new = 0
        for gift in gifts:
            gift_id = int(gift["gift_id"])
            title = gift.get("title") or str(gift_id)
            try:
                total_new += await self._scan_gift(
                    session=session,
                    gift_id=gift_id,
                    gift_title=title,
                    baseline=baseline,
                )
            except Exception as exc:
                if is_session_fatal(exc):
                    await session.mark_dead(exc)
                    break
                msg = str(exc)
                if isinstance(exc, ValueError) and "unsuccessful" in msg.lower():
                    logger.warning(
                        "Scan skipped [%s] gift %s (%s): %s",
                        session.label,
                        gift_id,
                        title,
                        msg,
                    )
                else:
                    logger.exception(
                        "Scan failed [%s] gift %s (%s)",
                        session.label,
                        gift_id,
                        title,
                    )
            await asyncio.sleep(self.settings.resale_request_delay_sec)
        session.clear_resolver_cache()
        return total_new

    async def _scan_gift(
        self,
        *,
        session: MarketSession,
        gift_id: int,
        gift_title: str,
        baseline: bool,
    ) -> int:
        client = await session.ensure()
        if not client:
            return 0

        watch = WatchFilter(
            gift_id=gift_id,
            gift_title=gift_title,
            models=[],
            backdrops=[],
            patterns=[],
        )
        scan_kwargs = dict(
            stars_only=self.settings.resale_stars_only,
            page_limit=self.settings.resale_page_limit,
            max_pages=self.settings.resale_max_pages,
            request_delay=self.settings.resale_request_delay_sec,
        )

        if baseline:
            total = 0
            async for page in iter_watch_listing_pages(client, watch, **scan_kwargs):
                slugs = [r["slug"] for r in page if r.get("slug")]
                await self.db.mark_slugs_seen_batch(slugs, gift_id)
                total += len(slugs)
            logger.info(
                "[gift %s] [%s] baseline remembered %s · %s",
                gift_id,
                session.label,
                total,
                gift_title,
            )
            return 0

        new_count = 0
        scanned = 0
        resolver = session.resolver

        async for page in iter_watch_listing_pages(client, watch, **scan_kwargs):
            scanned += len(page)
            slugs = [r["slug"] for r in page if r.get("slug")]
            seen = await self.db.get_seen_slugs(slugs)
            if slugs and len(seen) == len(slugs):
                break

            for listing in page:
                slug = listing.get("slug")
                if not slug or slug in seen:
                    continue

                if resolver:
                    await resolver.enrich_row(listing)

                if self.settings.notify_require_username:
                    if not normalize_owner_username(listing.get("owner_name")):
                        await self.db.mark_slug_seen(slug, gift_id)
                        continue

                max_lvl = self.settings.max_owner_level
                if max_lvl is not None:
                    level = listing.get("owner_level")
                    if level is not None and int(level) > max_lvl:
                        await self.db.mark_slug_seen(slug, gift_id)
                        continue

                claimed = await self.db.try_claim_slug(slug, gift_id)
                if not claimed:
                    continue

                ok = await self._notify(listing)
                if ok:
                    new_count += 1
                    logger.info(
                        "NEW [%s] → %s",
                        session.label,
                        _listing_line(listing, self.settings),
                    )
                else:
                    await self.db.unclaim_slug(slug)

        logger.debug(
            "[gift %s] [%s] scanned=%s new=%s · %s",
            gift_id,
            session.label,
            scanned,
            new_count,
            gift_title,
        )
        return new_count

    async def _notify(self, listing: dict) -> bool:
        band_key = band_key_for_listing(listing, self.settings)
        topic_id = self.topics.get(band_key) or self.topics.get(OTHER_BAND_KEY)
        if not topic_id:
            logger.warning("No topic for band %s", band_key)
            return False
        text = fmt.new_listing_text(listing, self.settings)
        slug = listing.get("slug") or ""
        kwargs: dict = {
            "chat_id": self.settings.alert_chat_id,
            "text": text,
            "message_thread_id": topic_id,
            "reply_markup": claim_kb(),
        }
        if slug:
            from aiogram.types import LinkPreviewOptions

            kwargs["link_preview_options"] = LinkPreviewOptions(
                is_disabled=False,
                url=f"https://t.me/nft/{slug}",
                prefer_large_media=True,
            )

        async with self._notify_lock:
            for attempt in range(_NOTIFY_MAX_RETRIES):
                gap = _NOTIFY_MIN_INTERVAL_SEC - (time.monotonic() - self._last_notify_at)
                if gap > 0:
                    await asyncio.sleep(gap)
                try:
                    await self.bot.send_message(**kwargs)
                    self._last_notify_at = time.monotonic()
                    return True
                except TelegramRetryAfter as exc:
                    wait = float(exc.retry_after) + 0.5
                    logger.warning(
                        "Flood control on alerts, sleep %.0fs (attempt %s/%s)",
                        wait,
                        attempt + 1,
                        _NOTIFY_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    self._last_notify_at = time.monotonic()
                except Exception:
                    logger.exception(
                        "Failed to send to chat %s topic %s",
                        self.settings.alert_chat_id,
                        topic_id,
                    )
                    return False
            logger.warning(
                "Gave up sending alert to topic %s after flood retries",
                topic_id,
            )
            return False

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from telethon import TelegramClient, functions, types

from parser.filters import (
    AttributeChoice,
    WatchFilter,
    gift_matches_watch,
    watch_api_attributes,
    watch_api_filter_label,
)
from parser.serializers import unique_listing_to_dict

logger = logging.getLogger(__name__)


async def _call_client(client: TelegramClient, request):
    last_exc: BaseException | None = None
    for attempt in range(3):
        try:
            return await client(request)
        except ConnectionError as exc:
            last_exc = exc
            if attempt >= 2:
                raise
            logger.warning("Telethon disconnected, reconnecting…")
            await client.connect()
            await asyncio.sleep(0.4 * (attempt + 1))
        except ValueError as exc:
            last_exc = exc
            if "unsuccessful" not in str(exc).lower() or attempt >= 2:
                raise
            logger.warning("Telegram request failed (%s), retry %s/3", exc, attempt + 2)
            try:
                await client.connect()
            except Exception:
                pass
            await asyncio.sleep(0.8 * (attempt + 1))
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


def _document_id(document: Any) -> int | None:
    return getattr(document, "id", None)


def _attr_to_choice(attr: Any) -> AttributeChoice | None:
    if isinstance(attr, types.StarGiftAttributeModel):
        doc_id = _document_id(attr.document)
        if doc_id is None:
            return None
        return AttributeChoice(kind="model", name=attr.name, document_id=doc_id)
    if isinstance(attr, types.StarGiftAttributePattern):
        doc_id = _document_id(attr.document)
        if doc_id is None:
            return None
        return AttributeChoice(kind="pattern", name=attr.name, document_id=doc_id)
    if isinstance(attr, types.StarGiftAttributeBackdrop):
        return AttributeChoice(
            kind="backdrop",
            name=attr.name,
            backdrop_id=attr.backdrop_id,
        )
    return None


async def fetch_gift_types(client: TelegramClient) -> list[dict]:
    result = await _call_client(client, functions.payments.GetStarGiftsRequest(hash=0))
    if isinstance(result, types.payments.StarGiftsNotModified):
        return []

    types_on_market: list[dict] = []
    for gift in result.gifts:
        resale_count = getattr(gift, "availability_resale", None) or 0
        if resale_count <= 0:
            continue
        types_on_market.append(
            {
                "gift_id": gift.id,
                "title": getattr(gift, "title", None) or "",
                "listings_on_market": resale_count,
            }
        )
    return types_on_market


async def fetch_resale_attributes(
    client: TelegramClient,
    gift_id: int,
    *,
    stars_only: bool,
) -> dict[str, list[dict]]:
    kwargs: dict = {
        "gift_id": gift_id,
        "offset": "",
        "limit": 1,
        "attributes_hash": 0,
    }
    if stars_only:
        kwargs["stars_only"] = True

    result = await _call_client(
        client, functions.payments.GetResaleStarGiftsRequest(**kwargs)
    )
    models: list[dict] = []
    backdrops: list[dict] = []
    patterns: list[dict] = []

    for attr in result.attributes or []:
        choice = _attr_to_choice(attr)
        if not choice:
            continue
        row = choice.to_dict()
        if choice.kind == "model":
            models.append(row)
        elif choice.kind == "backdrop":
            backdrops.append(row)
        elif choice.kind == "pattern":
            patterns.append(row)

    return {"models": models, "backdrops": backdrops, "patterns": patterns}


async def iter_watch_listing_pages(
    client: TelegramClient,
    watch: WatchFilter,
    *,
    stars_only: bool,
    page_limit: int,
    max_pages: int,
    request_delay: float,
) -> AsyncIterator[list[dict]]:
    offset = ""
    page = 0
    label = watch.label()
    api_filter = watch_api_filter_label(watch)

    logger.debug(
        "Scan start: %s (pages≤%s, limit=%s%s)",
        label,
        max_pages or "∞",
        page_limit,
        f", api_filter={api_filter}" if api_filter else "",
    )

    total = 0
    while True:
        if max_pages > 0 and page >= max_pages:
            logger.debug("Scan page limit reached (%s pages)", max_pages)
            break

        kwargs: dict = {
            "gift_id": watch.gift_id,
            "offset": offset,
            "limit": page_limit,
        }
        api_attrs = watch_api_attributes(watch)
        if api_attrs:
            kwargs["attributes"] = api_attrs
        if stars_only:
            kwargs["stars_only"] = True

        result = await _call_client(
            client, functions.payments.GetResaleStarGiftsRequest(**kwargs)
        )
        page_rows: list[dict] = []
        raw_count = 0
        skipped_ton = 0
        skipped_filter = 0

        for gift in result.gifts:
            if not isinstance(gift, types.StarGiftUnique):
                continue
            raw_count += 1
            if stars_only and getattr(gift, "resale_ton_only", False):
                skipped_ton += 1
                continue
            if not gift_matches_watch(gift, watch):
                skipped_filter += 1
                continue
            row = unique_listing_to_dict(gift)
            if row:
                page_rows.append(row)

        if page_rows:
            total += len(page_rows)
            yield page_rows

        extra = ""
        if skipped_ton or skipped_filter:
            extra = f" · ton_skip={skipped_ton} · filter_skip={skipped_filter}"
        logger.debug(
            "  page %s: api=%s matched=%s (total=%s)%s",
            page + 1,
            raw_count,
            len(page_rows),
            total,
            extra,
        )

        if not result.next_offset:
            break
        offset = result.next_offset
        page += 1
        await asyncio.sleep(request_delay)

    logger.debug("Scan done: %s listings for %s", total, label)

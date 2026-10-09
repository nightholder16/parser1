from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient, functions
from telethon.tl.types import PeerUser, User

from parser.serializers import normalize_owner_username

logger = logging.getLogger(__name__)


def peer_user_id(peer: Any) -> int | None:
    if isinstance(peer, PeerUser):
        return peer.user_id
    return None


class OwnerResolver:
    _MAX_CACHE = 5000

    def __init__(self, client: TelegramClient) -> None:
        self._client = client
        self._username_cache: dict[int, str | None] = {}
        self._profile_cache: dict[int, dict[str, Any]] = {}

    def clear_cache(self) -> None:
        self._username_cache.clear()
        self._profile_cache.clear()

    def _remember_username(self, uid: int, username: str | None) -> None:
        if len(self._username_cache) >= self._MAX_CACHE:
            self._username_cache.clear()
        self._username_cache[uid] = username

    def _remember_profile(self, uid: int, profile: dict[str, Any]) -> None:
        if len(self._profile_cache) >= self._MAX_CACHE:
            self._profile_cache.clear()
        self._profile_cache[uid] = profile

    async def enrich_page(self, pairs: list[tuple[Any, dict]]) -> None:
        for gift, row in pairs:
            await self.enrich_row(row, gift=gift)

    async def enrich_row(self, row: dict, gift: Any = None) -> None:
        if not normalize_owner_username(row.get("owner_name")):
            api_name = normalize_owner_username(
                getattr(gift, "owner_name", None) if gift is not None else None
            )
            if api_name:
                row["owner_name"] = api_name

        uid = row.get("owner_user_id")
        if uid is None and gift is not None:
            uid = peer_user_id(getattr(gift, "owner_id", None))
            if uid is not None:
                row["owner_user_id"] = uid
        if uid is None:
            return

        profile = await self._fetch_profile(int(uid))
        if profile.get("username"):
            row["owner_name"] = profile["username"]
        row["owner_premium"] = profile.get("premium")
        row["owner_level"] = profile.get("level")
        row["owner_paid_messages_stars"] = profile.get("paid_messages_stars")

    async def _fetch_profile(self, uid: int) -> dict[str, Any]:
        cached = self._profile_cache.get(uid)
        if cached is not None:
            return cached

        profile: dict[str, Any] = {
            "username": self._username_cache.get(uid),
            "premium": None,
            "level": None,
            "paid_messages_stars": None,
        }
        try:
            result = await self._client(functions.users.GetFullUserRequest(id=uid))
            full = result.full_user
            user = next((u for u in (result.users or []) if isinstance(u, User) and u.id == uid), None)
            if user is None and result.users:
                first = result.users[0]
                if isinstance(first, User):
                    user = first

            username = normalize_owner_username(getattr(user, "username", None) if user else None)
            premium = bool(getattr(user, "premium", False)) if user else None
            paid = getattr(full, "send_paid_messages_stars", None)
            rating = getattr(full, "stars_rating", None)
            level = getattr(rating, "level", None) if rating is not None else None

            profile = {
                "username": username,
                "premium": premium,
                "level": int(level) if level is not None else None,
                "paid_messages_stars": int(paid) if paid is not None else 0,
            }
            if username:
                self._remember_username(uid, username)
        except Exception:
            logger.debug("GetFullUser failed for user_id=%s", uid, exc_info=True)
            if profile["username"] is None and uid not in self._username_cache:
                try:
                    ent = await self._client.get_entity(uid)
                    username = normalize_owner_username(getattr(ent, "username", None))
                    premium = bool(getattr(ent, "premium", False))
                    profile["username"] = username
                    profile["premium"] = premium
                    self._remember_username(uid, username)
                except Exception:
                    logger.debug("get_entity failed for user_id=%s", uid, exc_info=True)
                    self._remember_username(uid, None)

        self._remember_profile(uid, profile)
        return profile

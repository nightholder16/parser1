from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def _parse_admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _parse_optional_int(raw: str | None) -> int | None:
    text = (raw or "").strip()
    if not text or text.lower() in ("off", "none", "false", "-"):
        return None
    return int(text)


Currency = Literal["stars", "ton"]


@dataclass(frozen=True)
class PriceBand:
    currency: Currency
    min_value: float
    max_value: float | None
    title: str

    @property
    def key(self) -> str:
        if self.max_value is None:
            return f"{self.currency}:{self.min_value:g}+"
        return f"{self.currency}:{self.min_value:g}-{self.max_value:g}"


def _default_bands() -> tuple[PriceBand, ...]:
    return (
        PriceBand("ton", 0, 5, "0-5 ton"),
        PriceBand("ton", 5, 10, "5-10 ton"),
        PriceBand("ton", 10, 20, "10-20 ton"),
        PriceBand("ton", 20, 50, "20-50 ton"),
        PriceBand("ton", 50, 100, "50-100 ton"),
        PriceBand("ton", 100, None, "100+ ton"),
    )


def _parse_bands(raw: str) -> tuple[PriceBand, ...]:
    if not raw.strip():
        return _default_bands()
    bands: list[PriceBand] = []
    for part in raw.split("|"):
        part = part.strip()
        if not part:
            continue
        chunks = part.split(":", 2)
        if len(chunks) != 3:
            raise ValueError(f"Bad TOPIC_BANDS entry: {part!r}")
        currency_raw, range_raw, title = chunks
        currency = currency_raw.strip().lower()
        if currency not in ("stars", "ton"):
            raise ValueError(f"Bad currency in TOPIC_BANDS: {currency}")
        range_raw = range_raw.strip()
        title = title.strip()
        if range_raw.endswith("+"):
            min_value = float(range_raw[:-1])
            max_value = None
        else:
            lo, hi = range_raw.split("-", 1)
            min_value = float(lo)
            max_value = float(hi)
        bands.append(
            PriceBand(
                currency=currency,  # type: ignore[arg-type]
                min_value=min_value,
                max_value=max_value,
                title=title,
            )
        )
    if not bands:
        return _default_bands()
    return tuple(bands)


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    admin_ids: frozenset[int]
    alert_chat_id: int
    sessions_dir: Path
    poll_interval_sec: int
    database_path: Path
    resale_page_limit: int
    resale_max_pages: int
    resale_stars_only: bool
    resale_request_delay_sec: float
    notify_require_username: bool
    max_owner_level: int | None
    stars_per_ton: float
    price_bands: tuple[PriceBand, ...]
    other_topic_title: str
    max_gifts_count: int | None = 25


    @classmethod
    def load(cls) -> Settings:
        db_path = Path(os.getenv("DATABASE_PATH", "data/alerts.db"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        sessions_dir = Path(os.getenv("SESSIONS_DIR", "data/sessions"))
        sessions_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            api_id=int(_require("API_ID")),
            api_hash=_require("API_HASH"),
            bot_token=_require("BOT_TOKEN"),
            admin_ids=_parse_admin_ids(),
            alert_chat_id=int(_require("ALERT_CHAT_ID")),
            sessions_dir=sessions_dir,
            poll_interval_sec=int(os.getenv("POLL_INTERVAL_SEC", "30")),
            database_path=db_path,
            resale_page_limit=int(os.getenv("RESALE_PAGE_LIMIT", "100")),
            resale_max_pages=int(os.getenv("RESALE_MAX_PAGES", "5")),
            resale_stars_only=os.getenv("RESALE_STARS_ONLY", "false").lower()
            in ("1", "true", "yes"),
            resale_request_delay_sec=float(os.getenv("RESALE_REQUEST_DELAY_SEC", "0.25")),
            notify_require_username=os.getenv("NOTIFY_REQUIRE_USERNAME", "true").lower()
            in ("1", "true", "yes"),
            max_owner_level=_parse_optional_int(os.getenv("MAX_OWNER_LEVEL", "6")),
            stars_per_ton=float(os.getenv("STARS_PER_TON", "100")),
            price_bands=_parse_bands(os.getenv("TOPIC_BANDS", "")),
            other_topic_title=os.getenv("OTHER_TOPIC_TITLE", "📦 Прочее").strip()
            or "📦 Прочее",
        max_gifts_count=int(os.getenv("MAX_GIFTS_COUNT", "25")),
        )
    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

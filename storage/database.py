from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()

    async def _migrate(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_listings (
                slug TEXT PRIMARY KEY,
                gift_id INTEGER,
                first_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_seen_gift ON seen_listings(gift_id);

            CREATE TABLE IF NOT EXISTS forum_topics (
                band_key TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            DROP TABLE IF EXISTS payment_orders;
            DROP TABLE IF EXISTS used_tx;
            DROP TABLE IF EXISTS subscriptions;

            CREATE TABLE IF NOT EXISTS temp_access (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id)
            );
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def get_meta(self, key: str) -> str | None:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            )
            row = await cur.fetchone()
            return str(row["value"]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            await self._conn.commit()

    async def is_baseline_done(self) -> bool:
        return (await self.get_meta("baseline_done")) == "1"

    async def mark_baseline_done(self) -> None:
        await self.set_meta("baseline_done", "1")

    async def get_topic_id(self, band_key: str) -> int | None:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(
                "SELECT topic_id FROM forum_topics WHERE band_key = ?",
                (band_key,),
            )
            row = await cur.fetchone()
            return int(row["topic_id"]) if row else None

    async def list_topics(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(
                "SELECT band_key, topic_id, title FROM forum_topics"
            )
            rows = await cur.fetchall()
            return {
                str(r["band_key"]): {
                    "topic_id": int(r["topic_id"]),
                    "title": str(r["title"]),
                }
                for r in rows
            }

    async def rekey_topic(self, old_key: str, new_key: str, title: str) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                """
                UPDATE forum_topics
                SET band_key = ?, title = ?, updated_at = ?
                WHERE band_key = ?
                """,
                (new_key, title, self._now(), old_key),
            )
            await self._conn.commit()

    async def upsert_topic(self, band_key: str, topic_id: int, title: str) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                """
                INSERT INTO forum_topics (band_key, topic_id, title, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(band_key) DO UPDATE SET
                    topic_id = excluded.topic_id,
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (band_key, topic_id, title, self._now()),
            )
            await self._conn.commit()

    async def get_seen_slugs(self, slugs: list[str]) -> set[str]:
        if not slugs:
            return set()
        async with self._lock:
            assert self._conn is not None
            out: set[str] = set()
            chunk_size = 400
            for i in range(0, len(slugs), chunk_size):
                chunk = slugs[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cur = await self._conn.execute(
                    f"SELECT slug FROM seen_listings WHERE slug IN ({placeholders})",
                    chunk,
                )
                rows = await cur.fetchall()
                out.update(str(r["slug"]) for r in rows)
            return out

    async def mark_slug_seen(self, slug: str, gift_id: int | None = None) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                """
                INSERT OR IGNORE INTO seen_listings (slug, gift_id, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (str(slug), gift_id, self._now()),
            )
            await self._conn.commit()

    async def try_claim_slug(self, slug: str, gift_id: int | None = None) -> bool:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(
                """
                INSERT OR IGNORE INTO seen_listings (slug, gift_id, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (str(slug), gift_id, self._now()),
            )
            await self._conn.commit()
            return (cur.rowcount or 0) > 0

    async def unclaim_slug(self, slug: str) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                "DELETE FROM seen_listings WHERE slug = ?",
                (str(slug),),
            )
            await self._conn.commit()

    async def mark_slugs_seen_batch(
        self, slugs: list[str], gift_id: int | None = None
    ) -> None:
        clean = [str(s) for s in slugs if s]
        if not clean:
            return
        async with self._lock:
            assert self._conn is not None
            now = self._now()
            gid = int(gift_id) if gift_id is not None else None
            await self._conn.executemany(
                """
                INSERT OR IGNORE INTO seen_listings (slug, gift_id, first_seen_at)
                VALUES (?, ?, ?)
                """,
                [(slug, gid, now) for slug in clean],
            )
            await self._conn.commit()

    async def set_temp_access(self, user_id: int, chat_id: int, expires_at: int) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                """
                INSERT INTO temp_access (user_id, chat_id, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET expires_at = excluded.expires_at
                """,
                (user_id, chat_id, expires_at),
            )
            await self._conn.commit()

    async def clear_temp_access(self, user_id: int, chat_id: int) -> None:
        async with self._lock:
            assert self._conn is not None
            await self._conn.execute(
                "DELETE FROM temp_access WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            await self._conn.commit()

    async def list_expired_access(self, now: int) -> list[tuple[int, int]]:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute(
                "SELECT user_id, chat_id FROM temp_access WHERE expires_at <= ?",
                (now,),
            )
            rows = await cur.fetchall()
            return [(int(r["user_id"]), int(r["chat_id"])) for r in rows]

    async def count_seen(self) -> int:
        async with self._lock:
            assert self._conn is not None
            cur = await self._conn.execute("SELECT COUNT(*) AS c FROM seen_listings")
            row = await cur.fetchone()
            return int(row["c"]) if row else 0

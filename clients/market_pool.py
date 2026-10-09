from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    SessionPasswordNeededError,
)
from telethon.tl.types.auth import SentCodePaymentRequired

from clients.market_session import MarketSession
from clients.session_errors import format_session_upload_error, is_session_fatal
from config import Settings
from parser.owners import OwnerResolver

logger = logging.getLogger(__name__)


def split_evenly(items: list[Any], parts: int) -> list[list[Any]]:
    if parts <= 0:
        return []
    if not items:
        return [[] for _ in range(parts)]
    base, extra = divmod(len(items), parts)
    chunks: list[list[Any]] = []
    start = 0
    for i in range(parts):
        size = base + (1 if i < extra else 0)
        chunks.append(items[start : start + size])
        start += size
    return chunks


class MarketSessionPool:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sessions: list[MarketSession] = []
        self._pending: dict[int, TelegramClient] = {}
        self._pending_path: dict[int, Path] = {}

    def discover_session_files(self) -> list[Path]:
        directory = self.settings.sessions_dir
        directory.mkdir(parents=True, exist_ok=True)
        return sorted(p for p in directory.glob("*.session") if p.is_file())

    def _create_client(self, base: Path) -> TelegramClient:
        return TelegramClient(
            str(base),
            self.settings.api_id,
            self.settings.api_hash,
            device_model="Android",
            system_version="14",
            app_version="10.9.2",
            lang_code="en",
            system_lang_code="en-US",
        )

    async def start(self) -> int:
        paths = self.discover_session_files()
        for path in paths:
            session = MarketSession(self.settings, path)
            try:
                await session.start()
                self.sessions.append(session)
            except Exception:
                logger.exception("Failed to load session %s", path.name)
        logger.info(
            "Session pool ready: %s/%s loaded from %s",
            len(self.sessions),
            len(paths),
            self.settings.sessions_dir,
        )
        return len(self.sessions)

    def alive(self) -> list[MarketSession]:
        return [s for s in self.sessions if not s._dead]

    def alive_count(self) -> int:
        return len(self.alive())

    def total_count(self) -> int:
        return len(self.sessions)

    def primary(self) -> MarketSession | None:
        alive = self.alive()
        return alive[0] if alive else None

    def get_by_label(self, label: str) -> MarketSession | None:
        for session in self.sessions:
            if session.label == label:
                return session
        return None

    def get_by_tg_user_id(self, tg_user_id: int) -> MarketSession | None:
        for session in self.sessions:
            if session.tg_user_id == tg_user_id:
                return session
        return None

    def list_info(self) -> list[dict[str, Any]]:
        return [
            {
                "label": session.label,
                "alive": session.is_alive(),
                "path": str(session.session_path),
                "tg_user_id": session.tg_user_id,
                "username": session.username,
            }
            for session in self.sessions
        ]

    def distribute(self, items: list[dict]) -> list[tuple[MarketSession, list[dict]]]:
        sessions = self.alive()
        if not sessions:
            return []
        chunks = split_evenly(items, len(sessions))
        return list(zip(sessions, chunks))

    async def cancel_auth(self, admin_id: int) -> None:
        client = self._pending.pop(admin_id, None)
        path = self._pending_path.pop(admin_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        if path is not None:
            session_file = Path(str(path) + ".session")
            if session_file.is_file():
                if not self.get_by_label(path.name):
                    session_file.unlink(missing_ok=True)
                    Path(str(session_file) + "-journal").unlink(missing_ok=True)

    async def start_phone_auth(self, admin_id: int, phone: str) -> str:
        await self.cancel_auth(admin_id)
        self.settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        base = self.settings.sessions_dir / f"_auth_{admin_id}"
        session_file = Path(str(base) + ".session")
        session_file.unlink(missing_ok=True)
        Path(str(session_file) + "-journal").unlink(missing_ok=True)

        client = self._create_client(base)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except PhoneNumberBannedError:
            await client.disconnect()
            return "banned"
        except FloodWaitError as exc:
            await client.disconnect()
            return f"flood:{exc.seconds}"
        except Exception as exc:
            await client.disconnect()
            logger.exception("send_code_request failed for admin %s", admin_id)
            return f"error:{exc}"

        if isinstance(sent, SentCodePaymentRequired):
            await client.disconnect()
            return "payment_required"

        self._pending[admin_id] = client
        self._pending_path[admin_id] = base
        return "code_sent"

    async def submit_code(self, admin_id: int, phone: str, code: str) -> str:
        client = self._pending.get(admin_id)
        if not client:
            return "no_pending"
        try:
            await client.sign_in(phone, code)
            return await self._finalize_pending(admin_id)
        except SessionPasswordNeededError:
            return "2fa_needed"
        except PhoneCodeInvalidError:
            return "bad_code"
        except PhoneCodeExpiredError:
            await self.cancel_auth(admin_id)
            return "expired"
        except Exception as exc:
            if is_session_fatal(exc):
                await self.cancel_auth(admin_id)
                return "dead"
            logger.exception("submit_code failed for admin %s", admin_id)
            return f"error:{exc}"

    async def submit_password(self, admin_id: int, password: str) -> str:
        client = self._pending.get(admin_id)
        if not client:
            return "no_pending"
        try:
            await client.sign_in(password=password)
            return await self._finalize_pending(admin_id)
        except Exception as exc:
            if is_session_fatal(exc):
                await self.cancel_auth(admin_id)
                return "dead"
            logger.exception("submit_password failed for admin %s", admin_id)
            await self.cancel_auth(admin_id)
            return f"error:{exc}"

    async def _finalize_pending(self, admin_id: int) -> str:
        client = self._pending.pop(admin_id, None)
        base = self._pending_path.pop(admin_id, None)
        if client is None or base is None:
            return "no_pending"
        try:
            me = await client.get_me()
        except Exception as exc:
            await client.disconnect()
            return f"error:{exc}"

        if self.get_by_tg_user_id(me.id):
            await client.disconnect()
            Path(str(base) + ".session").unlink(missing_ok=True)
            Path(str(base) + ".session-journal").unlink(missing_ok=True)
            return "duplicate"

        dest = self.settings.sessions_dir / f"{me.id}.session"
        src = Path(str(base) + ".session")
        await client.disconnect()

        if dest.exists():
            old = self.get_by_label(str(me.id))
            if old:
                await self.remove_session(str(me.id))
            else:
                dest.unlink(missing_ok=True)
                Path(str(dest) + "-journal").unlink(missing_ok=True)

        if src.is_file():
            src.rename(dest)
        Path(str(base) + ".session-journal").unlink(missing_ok=True)

        session = MarketSession(self.settings, dest)
        await session.start()
        session.tg_user_id = me.id
        session.username = me.username
        self.sessions.append(session)
        logger.info("Phone auth session added: %s (@%s)", me.id, me.username or "?")
        return "ok"

    async def add_session_file(self, source: Path, preferred_name: str | None = None) -> dict[str, Any]:
        self.settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        stem = (preferred_name or source.stem).strip() or source.stem
        stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem)[:64]
        dest = self.settings.sessions_dir / f"{stem}.session"
        if dest.exists() or self.get_by_label(stem):
            n = 2
            while True:
                candidate = self.settings.sessions_dir / f"{stem}_{n}.session"
                if not candidate.exists() and not self.get_by_label(candidate.stem):
                    dest = candidate
                    break
                n += 1

        shutil.copy2(source, dest)
        journal = Path(str(source) + "-journal")
        if journal.is_file():
            shutil.copy2(journal, Path(str(dest) + "-journal"))

        session = MarketSession(self.settings, dest)
        try:
            await session.start()
        except Exception as exc:
            dest.unlink(missing_ok=True)
            Path(str(dest) + "-journal").unlink(missing_ok=True)
            logger.exception("Failed to add session from %s", source)
            return {"ok": False, "error": format_session_upload_error(exc)}

        if session.tg_user_id is not None:
            existing = self.get_by_tg_user_id(session.tg_user_id)
            if existing and existing is not session:
                await session.shutdown()
                dest.unlink(missing_ok=True)
                Path(str(dest) + "-journal").unlink(missing_ok=True)
                return {"ok": False, "error": format_session_upload_error("Этот аккаунт уже добавлен")}

            renamed = self.settings.sessions_dir / f"{session.tg_user_id}.session"
            if renamed != dest and not renamed.exists() and not self.get_by_label(str(session.tg_user_id)):
                tg_user_id = session.tg_user_id
                username = session.username
                await session.shutdown()
                dest.rename(renamed)
                Path(str(dest) + "-journal").unlink(missing_ok=True)
                session = MarketSession(self.settings, renamed)
                await session.start()
                session.tg_user_id = tg_user_id
                session.username = username

        self.sessions.append(session)
        logger.info("Session added: %s", session.label)
        return {
            "ok": True,
            "label": session.label,
            "tg_user_id": session.tg_user_id,
            "username": session.username,
        }

    async def remove_session(self, label: str) -> bool:
        session = self.get_by_label(label)
        if session is None:
            return False
        await session.shutdown()
        self.sessions = [s for s in self.sessions if s.label != label]
        path = session.session_path
        if path.suffix != ".session":
            path = Path(str(path) + ".session")
        path.unlink(missing_ok=True)
        Path(str(path) + "-journal").unlink(missing_ok=True)
        logger.info("Session removed: %s", label)
        return True

    async def shutdown(self) -> None:
        for admin_id in list(self._pending.keys()):
            await self.cancel_auth(admin_id)
        for session in self.sessions:
            await session.shutdown()
        self.sessions.clear()

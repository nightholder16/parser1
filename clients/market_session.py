from __future__ import annotations

import logging
from pathlib import Path

from telethon import TelegramClient

from clients.session_errors import is_session_fatal, log_session_fatal
from config import Settings
from parser.owners import OwnerResolver

logger = logging.getLogger(__name__)


class MarketSession:
    def __init__(self, settings: Settings, session_path: Path) -> None:
        self.settings = settings
        self.session_path = session_path
        self.label = session_path.stem
        self.client: TelegramClient | None = None
        self.resolver: OwnerResolver | None = None
        self.tg_user_id: int | None = None
        self.username: str | None = None
        self._dead = False

    def _session_base(self) -> str:
        path = self.session_path
        if path.suffix == ".session":
            return str(path.with_suffix(""))
        return str(path)

    async def start(self) -> TelegramClient:
        if self._dead:
            raise RuntimeError("Market session is dead")
        base = self._session_base()
        session_file = Path(base + ".session")
        if not session_file.is_file():
            raise FileNotFoundError(f"Session file not found: {session_file}")
        client = TelegramClient(
            base,
            self.settings.api_id,
            self.settings.api_hash,
            device_model="Android",
            system_version="14",
            app_version="10.9.2",
            lang_code="en",
            system_lang_code="en-US",
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(f"Session not authorized: {session_file}")
        self.client = client
        self.resolver = OwnerResolver(client)
        me = await client.get_me()
        self.tg_user_id = me.id
        self.username = me.username
        logger.info(
            "Session ready [%s]: @%s id=%s",
            self.label,
            me.username or "?",
            me.id,
        )
        return client

    async def ensure(self) -> TelegramClient | None:
        if self._dead:
            return None
        if self.client is None:
            return None
        if self.client.is_connected():
            return self.client
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self.mark_dead()
                return None
            return self.client
        except Exception as exc:
            if is_session_fatal(exc):
                await self.mark_dead(exc)
            else:
                logger.exception("Failed to reconnect session [%s]", self.label)
            return None

    def is_alive(self) -> bool:
        return self.client is not None and not self._dead and self.client.is_connected()

    async def mark_dead(self, exc: BaseException | None = None) -> None:
        if self._dead:
            return
        self._dead = True
        if exc is not None:
            log_session_fatal(self._session_base(), exc)
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.resolver = None
        self.tg_user_id = None
        self.username = None
        logger.error("Session marked dead [%s]", self.label)

    def clear_resolver_cache(self) -> None:
        if self.resolver:
            self.resolver.clear_cache()

    async def shutdown(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.resolver = None

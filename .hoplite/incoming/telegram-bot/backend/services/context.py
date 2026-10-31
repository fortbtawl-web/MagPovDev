from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import Settings
from backend.integrations.payments import PaymentProvider
from backend.integrations.vpn import VpnRegistry

log = logging.getLogger("spiritt")


class ServiceError(Exception):
    """Business-rule violation with a user-facing Russian message."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.code = code


class Notifier(Protocol):
    async def send(self, user_id: int, text: str, *, buttons: list[tuple[str, str]] | None = None) -> None:
        """buttons: list of (text, url) rows."""


class NullNotifier:
    async def send(self, user_id: int, text: str, *, buttons: list[tuple[str, str]] | None = None) -> None:
        log.info("notify(%s) skipped: no bot configured", user_id)


@dataclass
class AppContext:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    vpn: VpnRegistry
    payments: PaymentProvider | None
    notifier: Notifier = field(default_factory=NullNotifier)
    extra: dict[str, Any] = field(default_factory=dict)

    def session(self) -> AsyncSession:
        return self.sessionmaker()

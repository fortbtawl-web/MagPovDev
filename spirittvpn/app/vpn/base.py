"""VPNProvider abstraction. Adapters must never log connection configs or keys."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class VPNError(Exception):
    """Non-retryable VPN error (bad request, auth failure, ...)."""


class VPNUnavailableError(VPNError):
    """Transient error: server down, timeout, 5xx. Safe to retry."""


class VPNNotConfiguredError(VPNError):
    """No real VPN infrastructure is connected."""


@dataclass(frozen=True)
class VPNClientState:
    external_id: str
    enabled: bool
    expires_at: datetime | None


@dataclass(frozen=True)
class VPNConnection:
    """What the user gets to connect. Either a subscription/connection link or a config file."""

    link: str | None = None
    config_text: str | None = None
    config_filename: str | None = None
    is_test: bool = False


class VPNProvider(ABC):
    name: str = "abstract"
    is_test: bool = False
    is_configured: bool = True

    @abstractmethod
    async def ensure_client(self, telegram_id: int, expires_at: datetime) -> VPNClientState:
        """Create the client if missing, otherwise enable it and set the expiry. Must be idempotent."""

    @abstractmethod
    async def suspend(self, telegram_id: int) -> None:
        """Disable access without deleting the client (restorable after renewal)."""

    @abstractmethod
    async def revoke(self, telegram_id: int) -> None:
        """Delete the client and invalidate its credentials."""

    @abstractmethod
    async def get_state(self, telegram_id: int) -> VPNClientState | None:
        """Current state on the VPN side, or None if the client does not exist."""

    @abstractmethod
    async def get_connection(self, telegram_id: int) -> VPNConnection:
        """Connection data for the user."""

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass

"""VPNService abstraction: one panel client per user device. Adapters must never log links or keys."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class VPNError(Exception):
    """Non-retryable VPN error (validation, auth failure, ...)."""


class VPNUnavailableError(VPNError):
    """Transient error: network, timeout, 5xx. The operation may or may not have been applied."""


class VPNNotConfiguredError(VPNError):
    """No real VPN infrastructure is connected."""


@dataclass(frozen=True)
class VPNClient:
    external_id: str
    username: str
    enabled: bool
    expires_at: datetime | None


@dataclass(frozen=True)
class VPNConnection:
    link: str
    is_test: bool = False


@dataclass(frozen=True)
class VPNNodeInfo:
    external_id: str
    name: str
    address: str
    country: str
    is_connected: bool
    is_disabled: bool
    users_online: int
    status_message: str | None


class VPNService(ABC):
    name: str = "abstract"
    is_test: bool = False
    is_configured: bool = True

    @abstractmethod
    async def create_client(
        self, username: str, telegram_id: int, expires_at: datetime, device_limit: int | None = None
    ) -> VPNClient:
        """Create a client. Not blindly retryable: on VPNUnavailableError call find_client() to recover."""

    @abstractmethod
    async def find_client(self, username: str) -> VPNClient | None: ...

    @abstractmethod
    async def get_client(self, external_id: str) -> VPNClient | None: ...

    @abstractmethod
    async def update_client(self, external_id: str, *, expires_at: datetime, enabled: bool) -> VPNClient:
        """Set absolute expiry and enabled state (idempotent)."""

    @abstractmethod
    async def revoke_client(self, external_id: str) -> None:
        """Delete the client so its credentials stop working. Missing client = success."""

    @abstractmethod
    async def get_connection(self, external_id: str) -> VPNConnection: ...

    @abstractmethod
    async def list_nodes(self) -> list[VPNNodeInfo]: ...

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


# Backwards-compatible name used across the codebase.
VPNProvider = VPNService

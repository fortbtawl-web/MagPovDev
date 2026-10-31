"""Internal VPN provider interface.

This is the application's abstraction, not a claim about any provider's API shape.
Concrete adapters (see h1vless.py) map it onto real, verified endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class VpnError(Exception):
    """Error talking to a VPN node. `retryable` means the outcome is unknown or transient."""

    def __init__(self, message: str, *, code: str = "error", retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status


class VpnNotConfigured(VpnError):
    def __init__(self, message: str = "VPN-нода не настроена"):
        super().__init__(message, code="not_configured", retryable=False)


@dataclass
class VpnClientInfo:
    name: str
    uuid: str | None
    banned: bool
    expires_at: datetime | None
    subscription_url: str | None
    link: str | None
    device_limit: int | None = None
    devices_count: int | None = None
    online: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def best_link(self) -> str | None:
        return self.subscription_url or self.link


@dataclass
class VpnHealth:
    ok: bool
    detail: str = ""
    clients: int | None = None


class VpnProvider(Protocol):
    code: str

    async def health_check(self) -> VpnHealth: ...

    async def create_client(self, name: str, *, expires_at: datetime, device_limit: int) -> VpnClientInfo:
        """Create a client. Must be idempotent by name: an existing client with this name is returned."""

    async def get_client(self, name: str) -> VpnClientInfo | None: ...

    async def extend_client(self, name: str, *, expires_at: datetime) -> VpnClientInfo:
        """Set the absolute expiry (idempotent)."""

    async def suspend_client(self, name: str, *, reason: str) -> None: ...

    async def resume_client(self, name: str) -> None: ...

    async def revoke_client(self, name: str) -> None:
        """Delete the client. Succeeds if it does not exist."""

    async def get_subscription_link(self, name: str) -> str | None: ...

    async def get_client_status(self, name: str) -> str:
        """'active' | 'banned' | 'expired' | 'missing'"""

    async def close(self) -> None: ...

"""Adapters without real infrastructure: 'none' (production placeholder) and 'test' (development only)."""
from __future__ import annotations

from datetime import datetime

from app.vpn.base import (
    VPNClientState,
    VPNConnection,
    VPNError,
    VPNNotConfiguredError,
    VPNProvider,
    VPNUnavailableError,
)


class NotConfiguredVPNProvider(VPNProvider):
    """Used until a real VPN server is connected. Never fabricates access."""

    name = "none"
    is_configured = False

    async def ensure_client(self, telegram_id: int, expires_at: datetime) -> VPNClientState:
        raise VPNNotConfiguredError

    async def suspend(self, telegram_id: int) -> None:
        raise VPNNotConfiguredError

    async def revoke(self, telegram_id: int) -> None:
        raise VPNNotConfiguredError

    async def get_state(self, telegram_id: int) -> VPNClientState | None:
        raise VPNNotConfiguredError

    async def get_connection(self, telegram_id: int) -> VPNConnection:
        raise VPNNotConfiguredError


class TestVPNProvider(VPNProvider):
    """In-memory adapter for development and tests. Its output is explicitly NOT a working VPN."""

    name = "test"
    is_test = True

    def __init__(self) -> None:
        self.clients: dict[int, VPNClientState] = {}
        self.fail_next: int = 0  # number of upcoming calls that raise VPNUnavailableError
        self.calls: list[tuple[str, int]] = []

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise VPNUnavailableError("test: server unavailable")

    async def ensure_client(self, telegram_id: int, expires_at: datetime) -> VPNClientState:
        self.calls.append(("ensure", telegram_id))
        self._maybe_fail()
        state = VPNClientState(external_id=f"test-{telegram_id}", enabled=True, expires_at=expires_at)
        self.clients[telegram_id] = state
        return state

    async def suspend(self, telegram_id: int) -> None:
        self.calls.append(("suspend", telegram_id))
        self._maybe_fail()
        if telegram_id in self.clients:
            old = self.clients[telegram_id]
            self.clients[telegram_id] = VPNClientState(old.external_id, False, old.expires_at)

    async def revoke(self, telegram_id: int) -> None:
        self.calls.append(("revoke", telegram_id))
        self._maybe_fail()
        self.clients.pop(telegram_id, None)

    async def get_state(self, telegram_id: int) -> VPNClientState | None:
        self._maybe_fail()
        return self.clients.get(telegram_id)

    async def get_connection(self, telegram_id: int) -> VPNConnection:
        self._maybe_fail()
        if telegram_id not in self.clients or not self.clients[telegram_id].enabled:
            raise VPNError("client is not active")
        return VPNConnection(
            config_text=(
                "# ⚠️ ТЕСТОВАЯ КОНФИГУРАЦИЯ SpirittVPN — НЕ РАБОЧИЙ VPN\n"
                "# Сгенерирована тестовым адаптером для проверки сценариев бота.\n"
                f"# client: test-{telegram_id}\n"
            ),
            config_filename="spirittvpn-TEST.txt",
            is_test=True,
        )

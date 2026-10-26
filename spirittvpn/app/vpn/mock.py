"""Adapters without real infrastructure: 'none' (placeholder) and 'mock' (development/tests only)."""
from __future__ import annotations

import itertools

from app.vpn.base import (
    VPNClient,
    VPNConnection,
    VPNError,
    VPNNodeInfo,
    VPNNotConfiguredError,
    VPNService,
    VPNUnavailableError,
)


class NotConfiguredVPNService(VPNService):
    """Used until a real VPN panel is connected. Never fabricates access."""

    name = "none"
    is_configured = False

    async def create_client(self, username, telegram_id, expires_at, device_limit=None):
        raise VPNNotConfiguredError

    async def find_client(self, username):
        raise VPNNotConfiguredError

    async def get_client(self, external_id):
        raise VPNNotConfiguredError

    async def update_client(self, external_id, *, expires_at, enabled):
        raise VPNNotConfiguredError

    async def revoke_client(self, external_id):
        raise VPNNotConfiguredError

    async def get_connection(self, external_id):
        raise VPNNotConfiguredError

    async def list_nodes(self):
        return []


class MockVPNService(VPNService):
    """In-memory panel for development and tests. Output is explicitly NOT a working VPN."""

    __test__ = False
    name = "mock"
    is_test = True

    def __init__(self) -> None:
        self.clients: dict[str, VPNClient] = {}
        self.fail_next: int = 0  # upcoming calls that raise VPNUnavailableError
        self.apply_then_fail: bool = False  # next create succeeds on the "panel" but the response is lost
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise VPNUnavailableError("mock: panel unavailable")

    async def create_client(self, username, telegram_id, expires_at, device_limit=None):
        self.calls.append(("create", username))
        self._maybe_fail()
        if any(c.username == username for c in self.clients.values()):
            raise VPNError("username already exists")
        client = VPNClient(str(next(self._ids)), username, True, expires_at)
        self.clients[client.external_id] = client
        if self.apply_then_fail:
            self.apply_then_fail = False
            raise VPNUnavailableError("mock: response lost")
        return client

    async def find_client(self, username):
        self.calls.append(("find", username))
        self._maybe_fail()
        return next((c for c in self.clients.values() if c.username == username), None)

    async def get_client(self, external_id):
        self._maybe_fail()
        return self.clients.get(external_id)

    async def update_client(self, external_id, *, expires_at, enabled):
        self.calls.append(("update", external_id))
        self._maybe_fail()
        old = self.clients.get(external_id)
        if old is None:
            raise VPNError("client not found")
        self.clients[external_id] = VPNClient(external_id, old.username, enabled, expires_at)
        return self.clients[external_id]

    async def revoke_client(self, external_id):
        self.calls.append(("revoke", external_id))
        self._maybe_fail()
        self.clients.pop(external_id, None)

    async def get_connection(self, external_id):
        self._maybe_fail()
        client = self.clients.get(external_id)
        if client is None or not client.enabled:
            raise VPNError("client is not active")
        return VPNConnection(link=f"https://mock.invalid/sub/TEST-NOT-A-REAL-VPN-{client.username}", is_test=True)

    async def list_nodes(self):
        self._maybe_fail()
        return [VPNNodeInfo("mock-1", "Mock node (тест)", "mock.invalid", "XX", True, False, len(self.clients), "test")]

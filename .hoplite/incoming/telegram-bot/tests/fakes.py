"""Controlled fakes for tests ONLY. Production code never imports this module."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Mapping

from backend.integrations.payments import CreatedPayment, PaymentError, PaymentState, WebhookEvent, WebhookRejected
from backend.integrations.vpn import VpnClientInfo, VpnError, VpnHealth


class FakeVpnNode:
    def __init__(self, code: str):
        self.code = code
        self.clients: dict[str, dict] = {}
        self.fail: dict[str, VpnError] = {}  # method -> error raised on every call until removed
        self.calls: list[tuple[str, str]] = []
        self.delay = 0.0

    def _check(self, method: str, name: str = "") -> None:
        self.calls.append((method, name))
        if method in self.fail:
            raise self.fail[method]

    def _info(self, name: str) -> VpnClientInfo:
        c = self.clients[name]
        return VpnClientInfo(
            name=name, uuid=c["uuid"], banned=c["banned"], expires_at=c["expires_at"],
            subscription_url=f"https://{self.code}.node.test/sub/{c['uuid']}", link=f"vless://{c['uuid']}@{self.code}.node.test:443", device_limit=c["device_limit"],
        )

    async def health_check(self) -> VpnHealth:
        self._check("health")
        return VpnHealth(ok=True, detail="ok", clients=len(self.clients))

    async def create_client(self, name: str, *, expires_at: datetime, device_limit: int) -> VpnClientInfo:
        if self.delay:
            await asyncio.sleep(self.delay)
        self._check("create", name)
        self.clients.setdefault(name, {"uuid": str(uuid.uuid4()), "banned": False, "expires_at": expires_at, "device_limit": device_limit})
        self.clients[name]["expires_at"] = expires_at
        return self._info(name)

    async def get_client(self, name: str) -> VpnClientInfo | None:
        self._check("get", name)
        return self._info(name) if name in self.clients else None

    async def extend_client(self, name: str, *, expires_at: datetime) -> VpnClientInfo:
        self._check("extend", name)
        if name not in self.clients:
            raise VpnError("user_not_found", code="user_not_found", status=404)
        self.clients[name]["expires_at"] = expires_at
        return self._info(name)

    async def suspend_client(self, name: str, *, reason: str) -> None:
        self._check("ban", name)
        self.clients[name]["banned"] = True

    async def resume_client(self, name: str) -> None:
        self._check("unban", name)
        self.clients[name]["banned"] = False

    async def revoke_client(self, name: str) -> None:
        self._check("revoke", name)
        self.clients.pop(name, None)

    async def get_subscription_link(self, name: str) -> str | None:
        info = await self.get_client(name)
        return info.best_link if info else None

    async def get_client_status(self, name: str) -> str:
        info = await self.get_client(name)
        return "missing" if info is None else ("banned" if info.banned else "active")

    async def close(self) -> None:
        pass


class FakePayments:
    name = "fake"

    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.fail_create = False

    async def create_payment(self, *, amount_minor: int, currency: str, description: str, order_public_id: str, idempotency_key: str) -> CreatedPayment:
        if self.fail_create:
            raise PaymentError("down", retryable=True)
        for pid, p in self.payments.items():
            if p["idem"] == idempotency_key:
                return CreatedPayment(pid, p["status"], f"https://pay.test/{pid}", p["amount"], p["currency"])
        pid = f"pay_{len(self.payments) + 1}"
        self.payments[pid] = {"idem": idempotency_key, "status": "pending", "amount": amount_minor, "currency": currency, "order": order_public_id, "paid": False}
        return CreatedPayment(pid, "pending", f"https://pay.test/{pid}", amount_minor, currency)

    def settle(self, pid: str, status: str = "succeeded", *, amount: int | None = None) -> None:
        p = self.payments[pid]
        p["status"] = status
        p["paid"] = status == "succeeded"
        if amount is not None:
            p["amount"] = amount

    async def get_payment(self, provider_payment_id: str) -> PaymentState:
        p = self.payments[provider_payment_id]
        return PaymentState(provider_payment_id, p["status"], p["amount"], p["currency"], p["paid"], {"order_id": p["order"]})

    def parse_webhook(self, *, body: bytes, headers: Mapping[str, str], client_ip: str | None) -> WebhookEvent:
        import json

        if headers.get("x-test-signature") != "valid":
            raise WebhookRejected("bad signature")
        data = json.loads(body)
        return WebhookEvent(event=data["event"], provider_payment_id=data["object"]["id"])

    async def refund(self, **kwargs) -> str:
        return "succeeded"

    async def close(self) -> None:
        pass


class FakeChannelChecker:
    """getChatMember stand-in: per-user status, or an exception to simulate Telegram errors."""

    def __init__(self, default: str = "member"):
        self.default = default
        self.statuses: dict[int, tuple[str, bool | None]] = {}
        self.error: Exception | None = None
        self.calls = 0

    async def status(self, user_id: int) -> tuple[str, bool | None]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.statuses.get(user_id, (self.default, None))


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send(self, user_id: int, text: str, *, buttons=None) -> None:
        self.sent.append((user_id, text))

"""Real adapters (H1//VLESS node, YooKassa) against mocked HTTP transports that follow the documented contracts."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from backend.integrations.payments.yookassa import YooKassaProvider, ip_allowed
from backend.integrations.payments import WebhookRejected
from backend.integrations.vpn import VpnError
from backend.integrations.vpn.h1vless import H1VlessProvider
from backend.utils import utcnow


def h1_node():
    clients: dict[str, dict] = {}
    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer node-token"
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/api")
        seen.append((request.method, path, body))
        payload = lambda c: {"uuid": c["uuid"], "name": c["name"], "status": "banned" if c["banned"] else "active", "expires_at": c["exp"], "subscription_url": f"https://n/sub/{c['uuid']}", "link": "vless://x", "device_limit": c["dl"]}  # noqa: E731
        if request.method == "GET" and path == "/health":
            return httpx.Response(200, json={"ok": True, "service": "vpn-api"})
        if request.method == "GET" and path == "/clients":
            return httpx.Response(200, json={"ok": True, "clients": [payload(c) for c in clients.values()]})
        if request.method == "POST" and path == "/create":
            if body["name"] in clients:
                return httpx.Response(409, json={"ok": False, "error": "user_already_exists"})
            clients[body["name"]] = {"uuid": "u-" + body["name"], "name": body["name"], "banned": False, "exp": 0, "dl": body.get("device_limit", 0)}
            return httpx.Response(201, json={"ok": True, "client": payload(clients[body["name"]])})
        if request.method == "PATCH" and path == "/edit":
            c = clients.get(body["name"])
            if not c:
                return httpx.Response(404, json={"ok": False, "error": "user_not_found"})
            c["exp"] = body["expires_at"]
            return httpx.Response(200, json={"ok": True, "client": payload(c)})
        if path.startswith("/clients/"):
            parts = path.split("/")
            name = parts[2]
            c = clients.get(name)
            if not c:
                return httpx.Response(404, json={"ok": False, "error": "user_not_found"})
            if request.method == "GET":
                return httpx.Response(200, json={"ok": True, "client": payload(c)})
            if request.method == "DELETE":
                clients.pop(name)
                return httpx.Response(200, json={"ok": True, "deleted": name})
            if request.method == "PATCH" and parts[3] in ("ban", "unban"):
                c["banned"] = parts[3] == "ban"
                return httpx.Response(200, json={"ok": True, "client": payload(c)})
        return httpx.Response(404, json={"ok": False, "error": "not_found"})

    return clients, seen, httpx.MockTransport(handler)


async def test_h1_full_lifecycle():
    clients, seen, transport = h1_node()
    p = H1VlessProvider("fi", "https://node.test:25626/api", "node-token", transport=transport)
    exp = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    info = await p.create_client("sv1x00", expires_at=exp, device_limit=1)
    assert info.expires_at == exp and info.subscription_url.endswith("u-sv1x00")
    assert seen[0][2] == {"name": "sv1x00", "days": 3, "device_limit": 1}
    # Idempotent retry: 409 -> fetch existing, re-pin expiry.
    again = await p.create_client("sv1x00", expires_at=exp, device_limit=1)
    assert again.uuid == info.uuid and len(clients) == 1
    await p.suspend_client("sv1x00", reason="test")
    assert await p.get_client_status("sv1x00") == "banned"
    await p.resume_client("sv1x00")
    assert (await p.health_check()).clients == 1
    await p.revoke_client("sv1x00")
    await p.revoke_client("sv1x00")  # already gone -> still success
    assert await p.get_client("sv1x00") is None
    await p.close()


async def test_h1_errors():
    def down(request):
        raise httpx.ConnectError("refused")

    p = H1VlessProvider("fi", "https://node.test/api", "t", transport=httpx.MockTransport(down))
    with pytest.raises(VpnError) as e:
        await p.get_client("abc")
    assert e.value.retryable
    assert not (await p.health_check()).ok

    p2 = H1VlessProvider("fi", "https://node.test/api", "t", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False, "error": "unauthorized"})))
    with pytest.raises(VpnError) as e:
        await p2.get_client("abc")
    assert e.value.code == "unauthorized" and not e.value.retryable
    with pytest.raises(VpnError):
        await p2.get_client("bad name!")
    unconfigured = H1VlessProvider("fi", "https://node.test/api", "")
    with pytest.raises(VpnError) as e:
        await unconfigured.get_client("abc")
    assert e.value.code == "not_configured"


def yk_transport(store: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "POST" and request.url.path == "/v3/payments":
            assert request.headers["idempotence-key"]
            body = json.loads(request.content)
            store["created"] = body
            return httpx.Response(200, json={"id": "2f-1", "status": "pending", "paid": False, "amount": body["amount"], "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/checkout/x"}, "metadata": body["metadata"], "test": True})
        if request.method == "GET" and request.url.path == "/v3/payments/2f-1":
            return httpx.Response(200, json={"id": "2f-1", "status": "succeeded", "paid": True, "amount": {"value": "250.00", "currency": "RUB"}, "metadata": {"order_id": "abc"}, "test": True})
        return httpx.Response(404, json={"type": "error", "code": "not_found"})

    return httpx.MockTransport(handler)


async def test_yookassa_create_and_get():
    store: dict = {}
    p = YooKassaProvider("shop", "secret", return_url="https://t.me/bot", transport=yk_transport(store))
    created = await p.create_payment(amount_minor=25000, currency="RUB", description="SpirittVPN — 3 месяца", order_public_id="abc", idempotency_key="abc")
    assert created.confirmation_url and created.amount_minor == 25000
    assert store["created"]["amount"] == {"value": "250.00", "currency": "RUB"} and store["created"]["capture"] is True
    state = await p.get_payment("2f-1")
    assert state.status == "succeeded" and state.paid and state.amount_minor == 25000 and state.metadata["order_id"] == "abc"
    await p.close()


def test_yookassa_webhook_ip_check():
    p = YooKassaProvider("shop", "secret", return_url="https://x")
    body = json.dumps({"type": "notification", "event": "payment.succeeded", "object": {"id": "2f-1"}}).encode()
    assert ip_allowed("185.71.76.5") and ip_allowed("77.75.156.11") and not ip_allowed("8.8.8.8")
    with pytest.raises(WebhookRejected):
        p.parse_webhook(body=body, headers={}, client_ip="8.8.8.8")
    ev = p.parse_webhook(body=body, headers={}, client_ip="185.71.76.5")
    assert ev.event == "payment.succeeded" and ev.provider_payment_id == "2f-1"
    with pytest.raises(WebhookRejected):
        p.parse_webhook(body=b"not json", headers={}, client_ip="185.71.76.5")

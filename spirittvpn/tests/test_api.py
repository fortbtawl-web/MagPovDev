"""Mini App API: initData auth, privacy, admin authorization, purchase and device flows over HTTP."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from app.api.common import ApiError, validate_init_data
from app.models import AuditLog, Order, Staff, Subscription, User
from app.payments.providers import TestPaymentProvider
from app.runtime import Deps, Logo
from app.services import settings_store
from app.vpn.mock import MockVPNService
from app.web import build_web_app
from tests.conftest import ADMIN_ID, BOT_TOKEN, make_plan


def init_data(user_id: int, *, token: str = BOT_TOKEN, auth_date: int | None = None, start_param: str | None = None,
              first_name: str = "Тест") -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH",
              "user": json.dumps({"id": user_id, "first_name": first_name, "username": f"u{user_id}"}, separators=(",", ":"))}
    if start_param:
        fields["start_param"] = start_param
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def create_invoice_link(self, **kw):
        return "https://t.me/$invoice"


@pytest.fixture
async def api(settings, session_factory):
    vpn = MockVPNService()
    payments = TestPaymentProvider("s3cret", "https://example.test")
    deps = Deps(settings, session_factory, vpn, payments, Logo(), bot_username="SpiritBot")
    client = TestClient(TestServer(build_web_app(deps, FakeBot())))
    await client.start_server()

    async def call(method, path, tg=42, body=None, **kw):
        headers = {"Authorization": f"tma {init_data(tg, **kw)}"}
        resp = await client.request(method, f"/api/v1{path}", json=body, headers=headers)
        return resp.status, await resp.json()

    call.client, call.vpn, call.deps = client, vpn, deps
    yield call
    await client.close()


def test_validate_init_data_rules():
    data = validate_init_data(init_data(7), BOT_TOKEN, 3600)
    assert data.user.id == 7
    with pytest.raises(ApiError):
        validate_init_data(init_data(7, token="999:OTHER"), BOT_TOKEN, 3600)
    with pytest.raises(ApiError):
        validate_init_data(init_data(7, auth_date=int(time.time()) - 7200), BOT_TOKEN, 3600)
    tampered = init_data(7).replace("%22id%22%3A7", "%22id%22%3A8")
    with pytest.raises(ApiError):
        validate_init_data(tampered, BOT_TOKEN, 3600)


async def test_requests_without_valid_session_are_rejected(api):
    resp = await api.client.get("/api/v1/me")
    assert resp.status == 401
    resp = await api.client.get("/api/v1/me", headers={"Authorization": "tma " + init_data(1, token="1:bad")})
    assert resp.status == 401


async def test_me_shows_own_data_and_no_secrets(api, settings):
    status, me = await api("GET", "/me", tg=555)
    assert status == 200 and me["user"]["telegram_id"] == 555
    assert me["subscription"]["status"] == "none" and me["subscription"]["expires_at"] is None
    assert me["trial"]["available"] and me["staff"] is None and me["vpn"]["test_mode"]
    raw = json.dumps(me)
    assert BOT_TOKEN not in raw and "s3cret" not in raw
    _, other = await api("GET", "/me", tg=556)
    assert other["user"]["telegram_id"] == 556


async def test_referral_via_start_param(api, session_factory):
    await api("GET", "/me", tg=1)
    await api("GET", "/me", tg=2, start_param="ref_1")
    await api("GET", "/me", tg=2, start_param="ref_3")
    async with session_factory() as s:
        u1 = await s.scalar(select(User).where(User.telegram_id == 1))
        u2 = await s.scalar(select(User).where(User.telegram_id == 2))
        assert u2.referrer_id == u1.id
    _, refs = await api("GET", "/referrals", tg=1)
    assert refs["link"] == "https://t.me/SpiritBot?start=ref_1" and refs["invited"] == 1
    assert "u2" not in json.dumps(refs) and '"2"' not in json.dumps(refs["invites"])


async def test_purchase_flow_and_duplicate_webhook(api, session_factory):
    async with session_factory() as s:
        await make_plan(s, code="duo30", price=20000, devices=2)
    status, body = await api("POST", "/orders", body={"plan_code": "duo30", "price": 1})
    assert status == 409 and body["error"] == "need_terms"
    assert (await api("POST", "/terms/accept", body={"version": "test-v1"}))[0] == 200
    status, body = await api("POST", "/orders", body={"plan_code": "duo30", "amount": 1})
    assert status == 201 and body["order"]["amount"] == 20000 and body["order"]["is_test"] and body["kind"] == "url"
    order_id = body["order"]["id"]
    provider: TestPaymentProvider = api.deps.payments
    payload, headers = provider.build_event(order_id, True, 20000, "RUB")
    for _ in range(2):
        r = await api.client.post("/payments/test/webhook", data=payload, headers=headers)
        assert r.status == 200
    _, o = await api("GET", f"/orders/{order_id}")
    assert o["order"]["status"] == "paid"
    _, me = await api("GET", "/me")
    assert me["subscription"]["status"] == "active" and me["subscription"]["device_limit"] == 2
    async with session_factory() as s:
        sub = await s.scalar(select(Subscription))
        assert (sub.expires_at - sub.starts_at).days == 30
    assert (await api("GET", f"/orders/{order_id}", tg=99))[0] == 404  # someone else's order


async def test_forged_webhook_rejected(api):
    r = await api.client.post("/payments/test/webhook", data=b'{"order_id":"x"}', headers={"X-Spiritt-Signature": "0" * 64})
    assert r.status == 401


async def test_devices_over_api_and_privacy(api):
    await api("POST", "/terms/accept", body={"version": "test-v1"})
    status, body = await api("POST", "/trial")
    assert status == 200
    _, devs = await api("GET", "/devices")
    assert devs["used"] == 1 and devs["limit"] == 1 and not devs["can_add"]
    dev_id = devs["devices"][0]["id"]
    status, conn = await api("GET", f"/devices/{dev_id}/connection")
    assert status == 200 and conn["is_test"] and conn["qr_svg"].lstrip().startswith("<svg")
    assert (await api("GET", f"/devices/{dev_id}/connection", tg=77))[0] == 404
    assert (await api("DELETE", f"/devices/{dev_id}", tg=77))[0] == 404
    status, _ = await api("POST", "/devices", body={"name": "Extra"})
    assert status == 409
    status, after = await api("DELETE", f"/devices/{dev_id}")
    assert status == 200 and after["used"] == 0 and api.vpn.clients == {}


async def test_trial_with_panel_down_reports_failure(api):
    await api("POST", "/terms/accept", body={"version": "test-v1"})
    api.vpn.fail_next = 10
    status, body = await api("POST", "/trial")
    assert status == 503 and body["error"] == "vpn_unavailable"
    _, me = await api("GET", "/me")
    assert me["subscription"]["status"] == "none" and me["trial"]["available"]


async def test_admin_api_authorization(api, session_factory):
    await api("GET", "/me", tg=5)
    assert (await api("GET", "/admin/stats", tg=5))[0] == 403
    assert (await api("POST", "/admin/users/5/extend", tg=5, body={"days": 999, "reason": "x"}))[0] == 403
    async with session_factory() as s:
        s.add(Staff(telegram_id=6, role="support"))
        await s.commit()
    assert (await api("GET", "/admin/users/5", tg=6))[0] == 200
    assert (await api("POST", "/admin/users/5/extend", tg=6, body={"days": 3, "reason": "x"}))[0] == 403
    status, body = await api("POST", "/admin/users/5/extend", tg=ADMIN_ID, body={"days": 3, "reason": ""})
    assert status == 400
    status, body = await api("POST", "/admin/users/5/extend", tg=ADMIN_ID, body={"days": 3, "reason": "gift"})
    assert status == 200
    _, me = await api("GET", "/me", tg=ADMIN_ID)
    assert me["staff"]["role"] == "owner" and "staff.manage" in me["staff"]["perms"]
    status, stats = await api("GET", "/admin/stats", tg=ADMIN_ID)
    assert status == 200 and stats["users_total"] == 3 and stats["vpn_test"]
    async with session_factory() as s:
        assert await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "manual_extend")) == 1
        assert await s.scalar(select(func.count(Order.id))) == 0


async def test_maintenance_blocks_user_actions(api, session_factory):
    async with session_factory() as s:
        await settings_store.set_setting(s, "maintenance", "on")
        await s.commit()
    status, body = await api("POST", "/trial")
    assert status == 503 and body["error"] == "maintenance"
    status, me = await api("GET", "/me")
    assert status == 200 and me["maintenance"]

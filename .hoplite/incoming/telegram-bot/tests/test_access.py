"""Mandatory documents + channel subscription: services, Mini App API and bot middleware."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
from sqlalchemy import func, select

from backend.api.app import create_app
from backend.models import TermsConsent, User
from backend.services import access
from backend.services.access import ChannelCheckError, is_subscribed
from backend.services.auth import issue_token
from backend.services.context import ServiceError
from bot.middlewares import GateMiddleware
from tests.conftest import ADMIN_ID, make_user


def h(ctx, uid, role="user"):
    return {"Authorization": f"Bearer {issue_token(ctx.settings.session_secret.get_secret_value(), user_id=uid, role=role, ttl_minutes=5)}"}


@pytest.fixture
async def client(ctx):
    app = create_app(ctx, run_worker=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_status_mapping():
    for st in ("creator", "administrator", "member"):
        assert is_subscribed(st, None)
    assert is_subscribed("restricted", True)
    assert not is_subscribed("restricted", False)
    assert not is_subscribed("left", None) and not is_subscribed("kicked", None) and not is_subscribed("weird", True)


async def test_terms_required_then_accepted(ctx):
    await make_user(ctx, 1, accept_terms=False)
    st = await access.gate_state(ctx, 1)
    assert not st.terms_accepted and not st.allowed
    with pytest.raises(access.AccessDenied) as e:
        await access.require_access(ctx, 1)
    assert e.value.code == "terms_required"
    with pytest.raises(ServiceError):
        await access.accept_terms(ctx, 1, "old-version", "bot")
    await access.accept_terms(ctx, 1, ctx.settings.terms_version, "bot")
    await access.accept_terms(ctx, 1, ctx.settings.terms_version, "bot")  # idempotent
    assert (await access.gate_state(ctx, 1)).allowed
    async with ctx.session() as s:
        rows = (await s.execute(select(TermsConsent))).scalars().all()
        user = await s.get(User, 1)
    assert len(rows) == 1 and rows[0].user_id == 1 and rows[0].version == ctx.settings.terms_version
    assert rows[0].privacy_url == "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
    assert rows[0].terms_url == "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"
    assert user.terms_accepted_at is not None


async def test_new_terms_version_requires_reacceptance(ctx):
    await make_user(ctx, 2)
    assert (await access.gate_state(ctx, 2)).allowed
    ctx.settings.terms_version = "2026-01-01"
    assert not (await access.gate_state(ctx, 2)).terms_accepted
    await access.accept_terms(ctx, 2, "2026-01-01", "miniapp")
    async with ctx.session() as s:
        assert await s.scalar(select(func.count(TermsConsent.id)).where(TermsConsent.user_id == 2)) == 1


@pytest.mark.parametrize(
    ("status", "is_member", "allowed"),
    [("member", None, True), ("administrator", None, True), ("creator", None, True), ("restricted", True, True), ("restricted", False, False), ("left", None, False), ("kicked", None, False)],
)
async def test_channel_statuses(ctx, status, is_member, allowed):
    await make_user(ctx, 3)
    ctx.extra["channel_checker"].statuses[3] = (status, is_member)
    st = await access.gate_state(ctx, 3, force_channel=True)
    assert st.allowed is allowed and st.channel_error is None


async def test_channel_error_denies_access(ctx):
    await make_user(ctx, 4)
    ctx.extra["channel_checker"].error = ChannelCheckError("TelegramBadRequest: member list is inaccessible")
    st = await access.gate_state(ctx, 4)
    assert not st.allowed and st.channel_subscribed is None and st.channel_error
    with pytest.raises(access.AccessDenied) as e:
        await access.require_access(ctx, 4)
    assert e.value.code == "channel_required"
    ctx.extra.pop("channel_checker")  # no bot client at all -> still denied
    assert not (await access.gate_state(ctx, 4, force_channel=True)).allowed


async def test_unsubscribe_later_is_detected(ctx):
    await make_user(ctx, 5)
    checker = ctx.extra["channel_checker"]
    assert (await access.gate_state(ctx, 5)).allowed
    calls = checker.calls
    assert (await access.gate_state(ctx, 5)).allowed and checker.calls == calls  # cached positive
    checker.statuses[5] = ("left", None)
    access.forget(ctx, 5)  # cache expiry
    assert not (await access.gate_state(ctx, 5)).allowed
    ctx.settings.channel_cache_seconds = 0
    checker.statuses[5] = ("member", None)
    assert (await access.gate_state(ctx, 5)).allowed
    checker.statuses[5] = ("kicked", None)
    assert not (await access.gate_state(ctx, 5)).allowed  # negative answers are never cached


async def test_admin_exempt_from_channel_only(ctx):
    await make_user(ctx, ADMIN_ID, accept_terms=False)
    ctx.extra["channel_checker"].error = ChannelCheckError("down")
    assert not (await access.gate_state(ctx, ADMIN_ID)).allowed  # documents still required
    await access.accept_terms(ctx, ADMIN_ID, ctx.settings.terms_version, "bot")
    assert (await access.gate_state(ctx, ADMIN_ID)).allowed


async def test_miniapp_gate_endpoints_and_protection(ctx, client):
    await make_user(ctx, 10, accept_terms=False)
    me = (await client.get("/api/me", headers=h(ctx, 10))).json()
    assert me["gate"]["terms_accepted"] is False and me["gate"]["allowed"] is False
    assert me["gate"]["privacy_url"].endswith("POLITIKA-KONFIDENCIALNOSTI-08-12-99")
    # Protected endpoints refuse with a machine-readable code.
    for method, url, body in [("POST", "/api/trial", None), ("POST", "/api/orders", {"plan_code": "dev1_30"}), ("POST", "/api/devices", {}), ("POST", "/api/promo/activate", {"code": "ANY"})]:
        r = await client.request(method, url, headers=h(ctx, 10), json=body)
        assert r.status_code == 403 and r.json()["code"] == "terms_required", url
    r = await client.post("/api/gate/accept-terms", headers=h(ctx, 10), json={"version": ctx.settings.terms_version})
    assert r.status_code == 200 and r.json()["gate"]["allowed"] is True
    ctx.extra["channel_checker"].statuses[10] = ("left", None)
    access.forget(ctx, 10)
    r = await client.post("/api/trial", headers=h(ctx, 10))
    assert r.status_code == 403 and r.json()["code"] == "channel_required"
    r = await client.post("/api/gate/check-channel", headers=h(ctx, 10))
    assert r.json()["gate"]["channel_subscribed"] is False and r.json()["gate"]["channel_url"] == "https://t.me/spirit_vpn_news"
    ctx.extra["channel_checker"].statuses[10] = ("member", None)
    r = await client.post("/api/gate/check-channel", headers=h(ctx, 10))
    assert r.json()["gate"]["allowed"] is True
    assert (await client.post("/api/trial", headers=h(ctx, 10))).status_code == 200


# ---------------- bot middleware: no bypass via commands or callbacks ----------------

def _tg_user(uid: int) -> TgUser:
    return TgUser(id=uid, is_bot=False, first_name="T")


def _msg(uid: int, text: str) -> Message:
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=uid, type="private"), from_user=_tg_user(uid), text=text)


def _cb(uid: int, data: str) -> CallbackQuery:
    return CallbackQuery(id="1", from_user=_tg_user(uid), chat_instance="c", data=data)


@pytest.fixture
def gate_calls(monkeypatch):
    calls = []

    async def fake_show_gate(event, ctx, st):
        calls.append((type(event).__name__, st.terms_accepted, st.allowed))

    import bot.handlers.gate as gate_mod

    monkeypatch.setattr(gate_mod, "show_gate", fake_show_gate)
    return calls


async def test_bot_middleware_blocks_until_allowed(ctx, gate_calls):
    await make_user(ctx, 20, accept_terms=False)
    mw = GateMiddleware()
    handled = []

    async def handler(event, data):
        handled.append(event)
        return "ok"

    data = {"ctx": ctx}
    # Direct callbacks / commands are blocked before documents are accepted.
    for ev in (_cb(20, "nav:plans"), _cb(20, "plan:buy:dev1_30"), _cb(20, "dev:link:1:"), _msg(20, "/menu"), _msg(20, "/promo"), _msg(20, "SOMECODE")):
        assert await mw(handler, ev, data) is None
    assert handled == [] and len(gate_calls) == 6 and all(c[1] is False for c in gate_calls)
    # /start and gate buttons always pass (they render the gate itself).
    assert await mw(handler, _msg(20, "/start"), data) == "ok"
    assert await mw(handler, _cb(20, "gate:accept"), data) == "ok"
    await access.accept_terms(ctx, 20, ctx.settings.terms_version, "bot")
    # Terms accepted but not subscribed -> still blocked.
    ctx.extra["channel_checker"].statuses[20] = ("left", None)
    assert await mw(handler, _cb(20, "nav:plans"), data) is None
    assert gate_calls[-1] == ("CallbackQuery", True, False)
    ctx.extra["channel_checker"].statuses[20] = ("member", None)
    assert await mw(handler, _cb(20, "nav:plans"), data) == "ok"

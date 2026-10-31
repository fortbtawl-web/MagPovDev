"""Roles, SpirittVPN ID, search, manual grants, blocking, support, cabinet isolation."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.api.app import create_app
from backend.integrations.vpn import VpnError
from backend.models import AdminRole, AuditLog, Device, SupportMessage, User, VpnOperation
from backend.services import admin as admin_svc
from backend.services import grants as grants_svc
from backend.services import roles
from backend.services import subscriptions as subs_svc
from backend.services import settings as settings_svc
from backend.services import support as support_svc
from backend.services.context import ServiceError
from backend.services.durations import add_months, parse_datetime, parse_spec, shift
from backend.services.auth import issue_token
from backend.utils import utcnow
from tests.conftest import ADMIN_ID, make_user
from tests.test_admin_api import init_data

OWNER = 8901325133
TZ = "Europe/Moscow"


@pytest.fixture
def owner_ctx(ctx):
    ctx.settings.owner_telegram_id = OWNER
    return ctx


@pytest.fixture
async def client(owner_ctx):
    app = create_app(owner_ctx, run_worker=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def h(ctx, uid, role="user"):
    return {"Authorization": f"Bearer {issue_token(ctx.settings.session_secret.get_secret_value(), user_id=uid, role=role, ttl_minutes=5)}"}


async def current(ctx, uid):
    async with ctx.session() as s:
        return await subs_svc.get_current(s, uid)


def node_of(ctx, device):
    return ctx.extra["nodes"][device.node.code]


async def devices(ctx, uid):
    from backend.services import devices as dev_svc

    async with ctx.session() as s:
        return await dev_svc.list_devices(s, uid, include_revoked=True)


# ---------------- SpirittVPN ID ----------------

async def test_public_id_unique_and_stable(ctx):
    first = await make_user(ctx, 1)
    pid = first.user.public_id
    assert re.fullmatch(r"SV-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}", pid)
    again = await make_user(ctx, 1, name="Новое имя")
    assert again.user.public_id == pid
    ids = {(await make_user(ctx, 100 + i)).user.public_id for i in range(40)}
    assert len(ids) == 40 and pid not in ids


# ---------------- roles ----------------

async def test_owner_from_env_and_username_is_not_trusted(owner_ctx):
    await make_user(owner_ctx, OWNER)
    async with owner_ctx.session() as s, s.begin():
        from backend.services import referrals as ref_svc

        # An impostor with the same username but another Telegram ID.
        await ref_svc.register_user(s, ref_svc.TgProfile(555, "cupsov", "fake"))
    assert await roles.get_role(owner_ctx, OWNER) == "owner"
    assert await roles.get_role(owner_ctx, 555) is None


async def test_setup_claim_owner_once(ctx):
    await make_user(ctx, 10)
    await make_user(ctx, 11)
    assert not await roles.owner_exists(ctx)
    code = roles.new_setup_code()
    with pytest.raises(ServiceError):
        await roles.claim_owner(ctx, 10, "wrong", code)
    await roles.claim_owner(ctx, 10, code, code)
    assert await roles.is_owner(ctx, 10)
    with pytest.raises(ServiceError):
        await roles.claim_owner(ctx, 11, code, code)


async def test_privilege_escalation_blocked(owner_ctx, client):
    await make_user(owner_ctx, OWNER)
    await make_user(owner_ctx, ADMIN_ID)
    await make_user(owner_ctx, 20)
    await make_user(owner_ctx, 21)
    # Env admin (not owner) cannot appoint admins.
    with pytest.raises(ServiceError):
        await roles.grant_admin(owner_ctx, ADMIN_ID, 20, "попытка", "web")
    r = await client.post("/api/admin/admins", headers=h(owner_ctx, ADMIN_ID, "admin"), json={"query": "20", "reason": "попытка"})
    assert r.status_code == 403
    # A regular user cannot reach admin routes at all, even with a forged role claim.
    for role in ("user", "admin"):
        r = await client.post("/api/admin/users/20/grant", headers=h(owner_ctx, 20, role), json={"mode": "lifetime", "reason": "сам себе"})
        assert r.status_code == 403
    # Owner appoints and revokes; revoked admin's token stops working immediately.
    r = await client.post("/api/admin/admins", headers=h(owner_ctx, OWNER, "admin"), json={"query": "21", "reason": "новый модератор"})
    assert r.status_code == 200, r.text
    assert (await client.get("/api/admin/stats", headers=h(owner_ctx, 21, "admin"))).status_code == 200
    # The new admin cannot appoint others or revoke the owner.
    assert (await client.post("/api/admin/admins", headers=h(owner_ctx, 21, "admin"), json={"query": "20", "reason": "друг"})).status_code == 403
    with pytest.raises(ServiceError):
        await roles.revoke_admin(owner_ctx, 21, OWNER, "переворот", "web")
    r = await client.post("/api/admin/admins/21/revoke", headers=h(owner_ctx, OWNER, "admin"), json={"reason": "больше не нужен"})
    assert r.status_code == 200
    assert (await client.get("/api/admin/stats", headers=h(owner_ctx, 21, "admin"))).status_code == 403
    # Env admins can't be revoked from the panel.
    with pytest.raises(ServiceError):
        await roles.revoke_admin(owner_ctx, OWNER, ADMIN_ID, "попытка", "web")
    async with owner_ctx.session() as s:
        actions = (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions == ["admin.grant", "admin.revoke"]


async def test_auth_returns_admin_token_for_owner(owner_ctx, client):
    r = await client.post("/api/auth/telegram", json={"init_data": init_data("123456:TEST_TOKEN", OWNER)})
    body = r.json()
    assert body["role"] == "owner" and body["admin_token"] and body["user"]["public_id"].startswith("SV-")
    r = await client.get("/api/admin/me", headers={"Authorization": f"Bearer {body['admin_token']}"})
    me = r.json()
    assert {k: me[k] for k in ("admin_id", "role", "is_owner")} == {"admin_id": OWNER, "role": "owner", "is_owner": True}
    assert "permissions" in me


# ---------------- search ----------------

async def test_search_by_all_identifiers(ctx):
    a = await make_user(ctx, 30)
    await make_user(ctx, 31)
    pid = a.user.public_id
    async with ctx.session() as s:
        assert [u.id for u in await admin_svc.find_users(s, "30")] == [30]
        assert [u.id for u in await admin_svc.find_users(s, "@user30")] == [30]
        assert [u.id for u in await admin_svc.find_users(s, pid.lower())] == [30]
        assert [u.id for u in await admin_svc.find_users(s, pid[3:])] == [30]
        assert {u.id for u in await admin_svc.find_users(s, "user3")} == {30, 31}
        assert await admin_svc.find_users(s, "%") == []
        assert (await admin_svc.resolve_user(s, pid)).id == 30
        assert (await admin_svc.resolve_user(s, "@USER31")).id == 31
        with pytest.raises(ServiceError):
            await admin_svc.resolve_user(s, "SV-ZZZZZZ")


# ---------------- durations ----------------

def test_duration_math():
    jan31 = datetime(2026, 1, 31, 12, 0, tzinfo=ZoneInfoMsk())
    assert add_months(jan31, 1).day == 28
    assert shift(datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc), 1, "months", TZ).day == 30
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert shift(base, 12, "hours", TZ) - base == timedelta(hours=12)
    assert shift(base, 2, "weeks", TZ) - base == timedelta(days=14)
    assert parse_spec("12h") == (12, "hours") and parse_spec("+3д") == (3, "days") and parse_spec("-1w") == (-1, "weeks")
    assert parse_spec("1m") == (1, "months") and parse_spec("abc") is None
    # Naive dates are interpreted in Moscow time and stored in UTC.
    assert parse_datetime("31.12.2026 23:00", TZ) == datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc)
    assert parse_datetime("2026-12-31T23:00:00+00:00", TZ).hour == 23


def ZoneInfoMsk():
    from zoneinfo import ZoneInfo

    return ZoneInfo(TZ)


# ---------------- grants ----------------

async def test_grant_custom_duration_and_extend_keeps_remaining(owner_ctx):
    await make_user(owner_ctx, 40)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 40, mode="extend", amount=12, unit="hours", reason="тест часов", device_limit=2)
    sub = res.subscription
    assert sub.status == "active" and sub.device_limit == 2 and not sub.is_lifetime
    assert abs((sub.expires_at - utcnow()) - timedelta(hours=12)) < timedelta(minutes=1)
    assert res.vpn["provisioned"] is True  # first device created and confirmed by the node
    first = sub.expires_at
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 40, mode="extend", amount=1, unit="months", reason="подарок", note="Подарок от администрации")
    assert res.subscription.expires_at == shift(first, 1, "months", TZ)
    d = (await devices(owner_ctx, 40))[0]
    assert node_of(owner_ctx, d).clients[d.client_name]["expires_at"] == res.subscription.expires_at
    assert any("Подарок от администрации" in t for uid, t in owner_ctx.notifier.sent if uid == 40)


async def test_grant_by_plan_set_end_and_replace(owner_ctx):
    await make_user(owner_ctx, 41)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 41, mode="extend", plan_code="dev2_30", reason="по тарифу")
    assert res.subscription.device_limit == 2
    assert abs((res.subscription.expires_at - utcnow()) - timedelta(days=30)) < timedelta(minutes=1)
    target = (utcnow() + timedelta(days=100)).replace(microsecond=0)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 41, mode="set_end", ends_at=target.isoformat(), reason="точная дата")
    assert res.subscription.expires_at == target
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 41, mode="replace", amount=7, unit="days", reason="замена срока")
    assert abs((res.subscription.expires_at - utcnow()) - timedelta(days=7)) < timedelta(minutes=1)
    with pytest.raises(ServiceError):
        await grants_svc.grant(owner_ctx, OWNER, "web", 41, mode="set_end", ends_at="01.01.2020", reason="прошлое")


async def test_lifetime_is_explicit_flag(owner_ctx):
    await make_user(owner_ctx, 42)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 42, mode="lifetime", reason="партнёр")
    sub = res.subscription
    assert sub.is_lifetime and sub.expires_at is None and sub.status == "active"
    with pytest.raises(ServiceError):
        await grants_svc.grant(owner_ctx, OWNER, "web", 42, mode="extend", amount=1, unit="days", reason="лишнее")
    async with owner_ctx.session() as s, s.begin():
        assert await subs_svc.expire_due(s) == []
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 42, mode="replace", amount=1, unit="weeks", reason="перевод на срок")
    assert not res.subscription.is_lifetime and res.subscription.expires_at is not None


async def test_extend_expired_starts_from_now(owner_ctx):
    await make_user(owner_ctx, 43)
    await grants_svc.grant(owner_ctx, OWNER, "web", 43, mode="extend", amount=3, unit="days", reason="старт")
    async with owner_ctx.session() as s, s.begin():
        from backend.models import Subscription

        await s.execute(update(Subscription).where(Subscription.user_id == 43).values(expires_at=utcnow() - timedelta(days=10)))
        await subs_svc.expire_due(s)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 43, mode="extend", amount=5, unit="days", reason="возврат")
    assert res.subscription.status == "active"
    assert abs((res.subscription.expires_at - utcnow()) - timedelta(days=5)) < timedelta(minutes=1)


async def test_revoke_and_regrant(owner_ctx):
    await make_user(owner_ctx, 44)
    await grants_svc.grant(owner_ctx, OWNER, "web", 44, mode="extend", amount=30, unit="days", reason="выдача")
    d = (await devices(owner_ctx, 44))[0]
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 44, mode="revoke", reason="нарушение")
    assert res.vpn["revoked"] == 1
    assert d.client_name not in node_of(owner_ctx, d).clients
    assert await current(owner_ctx, 44) is None
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 44, mode="replace", amount=2, unit="days", reason="повторная выдача")
    assert res.subscription.status == "active" and res.vpn["provisioned"] is True
    active = [x for x in await devices(owner_ctx, 44) if x.status == "active"]
    assert len(active) == 1 and active[0].client_name != d.client_name


async def test_terminate_bans_then_extend_restores(owner_ctx):
    await make_user(owner_ctx, 45)
    await grants_svc.grant(owner_ctx, OWNER, "web", 45, mode="extend", amount=30, unit="days", reason="выдача")
    d = (await devices(owner_ctx, 45))[0]
    node = node_of(owner_ctx, d)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 45, mode="terminate", reason="досрочно")
    assert res.subscription.status == "expired" and node.clients[d.client_name]["banned"]
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 45, mode="extend", amount=1, unit="weeks", reason="вернули")
    assert res.subscription.status == "active" and not node.clients[d.client_name]["banned"]
    assert (await devices(owner_ctx, 45))[0].status == "active"


async def test_shorten(owner_ctx):
    await make_user(owner_ctx, 46)
    r1 = await grants_svc.grant(owner_ctx, OWNER, "web", 46, mode="extend", amount=10, unit="days", reason="выдача")
    r2 = await grants_svc.grant(owner_ctx, OWNER, "web", 46, mode="shorten", amount=3, unit="days", reason="ошибка")
    assert r1.subscription.expires_at - r2.subscription.expires_at == timedelta(days=3)
    r3 = await grants_svc.grant(owner_ctx, OWNER, "web", 46, mode="shorten", amount=30, unit="days", reason="обнулить")
    assert r3.subscription.status == "expired"


async def test_grant_vpn_failure_then_recovery(owner_ctx, client):
    await make_user(owner_ctx, 47)
    for n in owner_ctx.extra["nodes"].values():
        n.fail["create"] = VpnError("timeout", code="timeout", retryable=True)
    res = await grants_svc.grant(owner_ctx, OWNER, "web", 47, mode="extend", amount=3, unit="days", reason="компенсация")
    assert res.vpn["provisioned"] is False and res.vpn["pending"] == 1 and "не всё" in res.message
    me = (await client.get("/api/me", headers=h(owner_ctx, 47))).json()
    assert me["access_state"] == "pending"
    for n in owner_ctx.extra["nodes"].values():
        n.fail.clear()
    async with owner_ctx.session() as s, s.begin():
        await s.execute(update(VpnOperation).values(next_retry_at=utcnow()))
    from backend.services import devices as dev_svc

    await dev_svc.retry_pending(owner_ctx)
    me = (await client.get("/api/me", headers=h(owner_ctx, 47))).json()
    assert me["access_state"] == "ready"
    async with owner_ctx.session() as s:
        assert await s.scalar(select(func.count(Device.id)).where(Device.user_id == 47)) == 1


async def test_grant_audit_and_reason_required(owner_ctx, client):
    await make_user(owner_ctx, 48)
    r = await client.post("/api/admin/users/48/grant", headers=h(owner_ctx, OWNER, "admin"), json={"mode": "extend", "amount": 2, "unit": "days", "reason": ""})
    assert r.status_code == 422
    r = await client.post(
        "/api/admin/users/48/grant", headers=h(owner_ctx, OWNER, "admin"),
        json={"mode": "extend", "amount": 2, "unit": "weeks", "reason": "внутренняя причина", "note": "Компенсация", "device_limit": 3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["subscription"]["device_limit"] == 3
    async with owner_ctx.session() as s:
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "subscription.extend"))
    assert log.admin_id == OWNER and log.reason == "внутренняя причина" and log.new_value["user_id"] == 48
    # The user sees the public note but never the internal reason.
    hist = (await client.get("/api/subscription/history", headers=h(owner_ctx, 48))).json()["items"]
    assert hist[0]["note"] == "Компенсация" and "comment" not in hist[0]
    detail = (await client.get("/api/admin/users/48", headers=h(owner_ctx, OWNER, "admin"))).json()
    assert detail["subscription_history"][0]["comment"] == "внутренняя причина"


# ---------------- blocking ----------------

async def test_block_and_unblock(owner_ctx, client):
    await make_user(owner_ctx, 50)
    await grants_svc.grant(owner_ctx, OWNER, "web", 50, mode="extend", amount=30, unit="days", reason="выдача")
    d = (await devices(owner_ctx, 50))[0]
    r = await client.post("/api/admin/users/50/block", headers=h(owner_ctx, OWNER, "admin"), json={"reason": "мошенничество"})
    assert r.status_code == 200 and r.json()["vpn"]["ok"] == 1
    assert node_of(owner_ctx, d).clients[d.client_name]["banned"]
    assert (await client.post("/api/devices", headers=h(owner_ctx, 50), json={})).status_code == 403
    me = (await client.get("/api/me", headers=h(owner_ctx, 50))).json()
    assert me["account_status"] == "banned"
    # Support stays available to blocked users.
    r = await client.post("/api/support/tickets", headers=h(owner_ctx, 50), json={"category": "other", "text": "Почему меня заблокировали?"})
    assert r.status_code == 200
    await admin_svc.set_blocked(owner_ctx, OWNER, "web", 50, "ошибка", blocked=False)
    assert not node_of(owner_ctx, d).clients[d.client_name]["banned"]
    assert (await current(owner_ctx, 50)).status == "active"
    with pytest.raises(ServiceError):
        await admin_svc.set_blocked(owner_ctx, OWNER, "web", ADMIN_ID, "админа нельзя", blocked=True)


# ---------------- support & isolation ----------------

async def test_support_flow_and_isolation(owner_ctx, client):
    await make_user(owner_ctx, OWNER)
    await make_user(owner_ctx, 60)
    await make_user(owner_ctx, 61)
    r = await client.post("/api/support/tickets", headers=h(owner_ctx, 60), json={"category": "connection", "text": "Не подключается VPN на iPhone"})
    assert r.status_code == 200, r.text
    t = r.json()["ticket"]
    assert re.fullmatch(r"SUP-\d{5,7}", t["number"]) and t["status"] == "new"
    assert any("Обращение" in text for uid, text in owner_ctx.notifier.sent if uid == OWNER)
    # Another user cannot read, reply to or close it (same 404 as a missing ticket).
    for call in (
        client.get(f"/api/support/tickets/{t['id']}", headers=h(owner_ctx, 61)),
        client.post(f"/api/support/tickets/{t['id']}/messages", headers=h(owner_ctx, 61), json={"text": "чужой"}),
        client.post(f"/api/support/tickets/{t['id']}/close", headers=h(owner_ctx, 61)),
    ):
        assert (await call).status_code == 400
    assert (await client.get("/api/support/tickets", headers=h(owner_ctx, 61))).json()["tickets"] == []
    # Internal note is hidden from the user; public reply goes to the ticket owner only.
    owner_ctx.notifier.sent.clear()
    await support_svc.admin_reply(owner_ctx, OWNER, t["id"], "Проверяем логи ноды", internal=True)
    r = await client.post(f"/api/admin/support/tickets/{t['id']}/reply", headers=h(owner_ctx, OWNER, "admin"), json={"text": "Обновите приложение", "internal": False})
    assert r.status_code == 200 and r.json()["ticket"]["status"] == "waiting_user"
    assert [uid for uid, _ in owner_ctx.notifier.sent] == [60]
    thread = (await client.get(f"/api/support/tickets/{t['id']}", headers=h(owner_ctx, 60))).json()
    texts = [m["text"] for m in thread["messages"]]
    assert "Проверяем логи ноды" not in texts and "Обновите приложение" in texts
    assert all(m["author"] != "admin" for m in thread["messages"])
    # User reply moves it back to work.
    r = await client.post(f"/api/support/tickets/{t['id']}/messages", headers=h(owner_ctx, 60), json={"text": "Обновил, работает"})
    assert r.json()["ticket"]["status"] == "in_progress"
    r = await client.post(f"/api/admin/support/tickets/{t['id']}/status", headers=h(owner_ctx, OWNER, "admin"), json={"status": "resolved"})
    assert r.json()["ticket"]["status"] == "resolved"
    with pytest.raises(ServiceError):
        await support_svc.assign(owner_ctx, OWNER, t["id"], 61)  # not an admin
    await support_svc.assign(owner_ctx, OWNER, t["id"], ADMIN_ID)
    full = (await client.get(f"/api/admin/support/tickets/{t['id']}", headers=h(owner_ctx, OWNER, "admin"))).json()
    assert full["ticket"]["assigned_admin_id"] == ADMIN_ID
    assert any(m["is_internal"] for m in full["messages"])
    # Users cannot reach the admin support API.
    assert (await client.get("/api/admin/support/tickets", headers=h(owner_ctx, 60, "admin"))).status_code == 403
    async with owner_ctx.session() as s:
        assert await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action.like("support.%"))) >= 2


async def test_support_limits(owner_ctx):
    await make_user(owner_ctx, 62)
    async with owner_ctx.session() as s, s.begin():
        await settings_svc.put(s, "support_daily_limit", 10)  # isolate the open-tickets limit from the daily one
    for i in range(3):
        await support_svc.create_ticket(owner_ctx, 62, "other", f"Вопрос номер {i} подробно")
    with pytest.raises(ServiceError) as e:
        await support_svc.create_ticket(owner_ctx, 62, "other", "Четвёртый вопрос подробно")
    assert e.value.code == "too_many_open"
    with pytest.raises(ServiceError):
        await support_svc.create_ticket(owner_ctx, 62, "hack", "Неверная категория тут")
    async with owner_ctx.session() as s:
        with pytest.raises(Exception):
            async with s.begin():
                s.add(SupportMessage(ticket_id=1, author_type="user", body="x", is_internal=True))


async def test_cabinet_isolation(owner_ctx, client):
    a = await make_user(owner_ctx, 70)
    await make_user(owner_ctx, 71)
    await grants_svc.grant(owner_ctx, OWNER, "web", 70, mode="extend", amount=30, unit="days", reason="выдача")
    me = (await client.get("/api/me", headers=h(owner_ctx, 71))).json()
    assert me["user"]["id"] == 71 and me["subscription"] is None and me["user"]["public_id"] != a.user.public_id
    dev = (await devices(owner_ctx, 70))[0]
    assert (await client.get(f"/api/devices/{dev.id}/link", headers=h(owner_ctx, 71))).status_code == 400
    assert (await client.get("/api/subscription/history", headers=h(owner_ctx, 71))).json()["items"] == []
    # No endpoint accepts a user id from the client: /api/me ignores query parameters.
    assert (await client.get("/api/me?user_id=70", headers=h(owner_ctx, 71))).json()["user"]["id"] == 71
    me70 = (await client.get("/api/me", headers=h(owner_ctx, 70))).json()
    assert me70["subscription"]["remaining_seconds"] > 29 * 86400 and me70["access_state"] == "ready"


async def test_grant_owner_role(owner_ctx, client):
    await make_user(owner_ctx, OWNER)
    await make_user(owner_ctx, ADMIN_ID)
    await make_user(owner_ctx, 80)
    await make_user(owner_ctx, 81)
    with pytest.raises(ServiceError):  # a plain admin cannot hand out ownership
        await roles.grant_owner(owner_ctx, ADMIN_ID, 80, "попытка", "web")
    r = await client.post("/api/admin/admins", headers=h(owner_ctx, ADMIN_ID, "admin"), json={"query": "80", "role": "owner", "reason": "попытка"})
    assert r.status_code == 403
    await roles.grant_admin(owner_ctx, OWNER, 80, "сначала админ", "web")
    await roles.grant_owner(owner_ctx, OWNER, 80, "повышение до владельца", "web")  # admin -> owner
    await roles.grant_owner(owner_ctx, OWNER, 81, "второй владелец", "web")  # several DB owners are allowed
    assert await roles.get_role(owner_ctx, 80) == "owner" and await roles.get_role(owner_ctx, 81) == "owner"
    # A DB owner can use owner-only routes.
    r = await client.post("/api/admin/admins", headers=h(owner_ctx, 80, "admin"), json={"query": str(ADMIN_ID), "reason": "проверка прав"})
    assert r.status_code == 400  # reached the service: ADMIN_ID is already an admin
    with pytest.raises(ServiceError):
        await roles.grant_owner(owner_ctx, OWNER, 81, "повтор", "web")
    async with owner_ctx.session() as s:
        actions = (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions.count("admin.grant_owner") == 2

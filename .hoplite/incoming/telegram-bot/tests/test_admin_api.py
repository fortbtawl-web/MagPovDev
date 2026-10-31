"""Admin access, plan management, audit; Mini App auth over HTTP."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from backend.api.app import create_app
from backend.models import AuditLog, Plan
from backend.services import admin as admin_svc
from backend.services.auth import AuthError, issue_token, validate_init_data
from backend.services.context import ServiceError
from tests.conftest import ADMIN_ID, make_user


def init_data(token: str, user_id: int, *, auth_date: int | None = None, start_param: str | None = None) -> str:
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAE", "user": json.dumps({"id": user_id, "first_name": "Тест", "username": "t"})}
    if start_param:
        fields["start_param"] = start_param
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
async def client(ctx):
    app = create_app(ctx, run_worker=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def bearer(ctx, uid, role):
    return {"Authorization": f"Bearer {issue_token(ctx.settings.session_secret.get_secret_value(), user_id=uid, role=role, ttl_minutes=5)}"}


def test_init_data_validation():
    token = "123456:TEST_TOKEN"
    user = validate_init_data(init_data(token, 42), token, max_age_seconds=3600)
    assert user["id"] == 42
    tampered = init_data(token, 42).replace("42", "43")
    with pytest.raises(AuthError):
        validate_init_data(tampered, token, max_age_seconds=3600)
    with pytest.raises(AuthError):
        validate_init_data(init_data(token, 42, auth_date=int(time.time()) - 7200), token, max_age_seconds=3600)
    with pytest.raises(AuthError):
        validate_init_data(init_data("999:OTHER", 42), token, max_age_seconds=3600)


async def test_miniapp_auth_and_me(client, ctx):
    inviter = await make_user(ctx, 500)
    r = await client.post("/api/auth/telegram", json={"init_data": init_data("123456:TEST_TOKEN", 501, start_param=f"ref_{inviter.user.referral_code}")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_admin"] is False and body["admin_token"] is None
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    data = me.json()
    assert data["user"]["id"] == 501 and data["subscription"] is None and data["trial"]["available"]
    assert data["referral_link"].startswith("https://t.me/SpirittTestBot?start=ref_")
    ref = await client.get("/api/referrals", headers=bearer(ctx, 500, "user"))
    assert ref.json()["summary"]["invited"] == 1


async def test_miniapp_rejects_forged_init_data(client):
    r = await client.post("/api/auth/telegram", json={"init_data": init_data("999:WRONG", 1)})
    assert r.status_code == 401
    r = await client.get("/api/me")
    assert r.status_code == 401
    r = await client.get("/api/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


async def test_miniapp_trial_and_devices_flow(client, ctx):
    await make_user(ctx, 510)
    h = bearer(ctx, 510, "user")
    r = await client.post("/api/trial", headers=h)
    assert r.status_code == 200, r.text
    devs = (await client.get("/api/devices", headers=h)).json()
    assert devs["used"] == 1 and devs["limit"] == 1
    dev_id = devs["devices"][0]["id"]
    link = (await client.get(f"/api/devices/{dev_id}/link", headers=h)).json()
    assert link["live"] and link["subscription_url"]
    r = await client.post("/api/devices", headers=h, json={})
    assert r.status_code == 400 and "лимит" in r.json()["detail"].lower()
    # Another user cannot see or revoke it.
    await make_user(ctx, 511)
    r = await client.delete(f"/api/devices/{dev_id}", headers=bearer(ctx, 511, "user"))
    assert r.status_code == 400
    r = await client.delete(f"/api/devices/{dev_id}", headers=h)
    assert r.json()["confirmed"] is True


async def test_admin_allowed_and_user_denied(client, ctx):
    r = await client.get("/api/admin/stats", headers=bearer(ctx, ADMIN_ID, "admin"))
    assert r.status_code == 200 and "users_total" in r.json()["stats"]
    assert (await client.get("/api/admin/stats", headers=bearer(ctx, 777, "user"))).status_code == 403
    # A forged admin role for a non-admin ID is still refused.
    assert (await client.get("/api/admin/stats", headers=bearer(ctx, 777, "admin"))).status_code == 403
    # An admin's plain user token is not enough either.
    assert (await client.get("/api/admin/stats", headers=bearer(ctx, ADMIN_ID, "user"))).status_code == 403


async def test_admin_change_plan_audited(client, ctx):
    async with ctx.session() as s:
        plan = await s.scalar(select(Plan).where(Plan.code == "m3"))
    r = await client.patch(
        f"/api/admin/plans/{plan.id}", headers=bearer(ctx, ADMIN_ID, "admin"), json={"changes": {"device_limit": 3, "price_minor": 29900}, "reason": "новая цена"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["device_limit"] == 3
    async with ctx.session() as s:
        log = await s.scalar(select(AuditLog).where(AuditLog.action == "plan.update"))
    assert log.admin_id == ADMIN_ID and log.reason == "новая цена"
    assert log.old_value["price_minor"] == 25000 and log.new_value["price_minor"] == 29900


async def test_admin_reason_required(client, ctx):
    await make_user(ctx, 520)
    r = await client.post("/api/admin/users/520/bonus", headers=bearer(ctx, ADMIN_ID, "admin"), json={"days": 3, "reason": ""})
    assert r.status_code == 422
    with pytest.raises(ServiceError):
        await admin_svc.adjust_bonus(ctx, ADMIN_ID, "bot", 520, 3, "  ")
    async with ctx.session() as s:
        assert await s.scalar(select(AuditLog)) is None


async def test_manual_bonus_and_reversal_audited(ctx):
    from backend.services import referrals as ref_svc
    from backend.services import trial as trial_svc
    from backend.models import ReferralReward

    a = await make_user(ctx, 530)
    await make_user(ctx, 531, f"ref_{a.user.referral_code}")
    await trial_svc.activate_trial(ctx, 531)
    assert await admin_svc.adjust_bonus(ctx, ADMIN_ID, "bot", 530, 10, "акция") == 13
    with pytest.raises(ServiceError):
        await admin_svc.adjust_bonus(ctx, ADMIN_ID, "bot", 530, -100, "ошибка")
    async with ctx.session() as s:
        reward = await s.scalar(select(ReferralReward))
    await admin_svc.reverse_reward(ctx, ADMIN_ID, "web", reward.id, "мультиаккаунт")
    with pytest.raises(ServiceError):
        await admin_svc.reverse_reward(ctx, ADMIN_ID, "web", reward.id, "повтор")
    async with ctx.session() as s:
        assert await ref_svc.bonus_balance(s, 530) == 10
        actions = (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert actions == ["bonus.adjust", "referral.reverse_reward"]


async def test_admin_settings_validation(ctx):
    with pytest.raises(ServiceError):
        await admin_svc.update_setting(ctx, ADMIN_ID, "web", "trial_days", 999, "слишком много")
    assert await admin_svc.update_setting(ctx, ADMIN_ID, "web", "trial_days", "5", "акция") == 5


async def test_admin_login_code(ctx):
    from backend.services.auth import request_admin_code, verify_admin_code

    await request_admin_code(ctx, 4242)  # not an admin: nothing sent
    assert ctx.notifier.sent == []
    await request_admin_code(ctx, ADMIN_ID)
    uid, text = ctx.notifier.sent[-1]
    code = text.split("<code>")[1].split("</code>")[0]
    assert uid == ADMIN_ID
    assert not await verify_admin_code(ctx, ADMIN_ID, "000000" if code != "000000" else "111111")
    assert await verify_admin_code(ctx, ADMIN_ID, code)
    assert not await verify_admin_code(ctx, ADMIN_ID, code)  # single use


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json()["db"] is True

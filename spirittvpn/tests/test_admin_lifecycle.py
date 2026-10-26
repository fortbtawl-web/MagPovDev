from datetime import timedelta

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select

from app.models import AuditLog, BonusEntry, Staff, Subscription, SubStatus
from app.services import admin as svc
from app.services.devices import sync_user_vpn
from app.services.ops import expire_subscriptions, reconcile_vpn, retry_vpn_sync, send_expiry_notices
from app.services.roles import AdminPermissionError, get_role, perms_of
from tests.conftest import ADMIN_ID, NOW, client_of, make_user, trial

fmt = lambda dt: dt.isoformat()  # noqa: E731


class Recorder:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def __call__(self, tg: int, text: str, kb: InlineKeyboardMarkup | None) -> bool:
        self.sent.append((tg, text))
        return True


# ---------- roles ----------

async def test_non_staff_is_denied_everywhere(session, settings):
    await make_user(session, tg_id=5)
    for coro in (
        svc.stats(session, settings, 5, NOW),
        svc.manual_extend(session, settings, 5, 5, 30, "hack", NOW),
        svc.create_promo(session, settings, 5, "FREE999", "days", 999, []),
        svc.change_setting(session, settings, 5, "maintenance", "on"),
        svc.list_staff(session, settings, 5),
    ):
        with pytest.raises(AdminPermissionError):
            await coro
    assert await session.scalar(select(Subscription)) is None


async def test_role_permissions(session, settings):
    session.add_all([Staff(telegram_id=7, role="support"), Staff(telegram_id=8, role="tech"), Staff(telegram_id=9, role="admin")])
    await session.commit()
    await make_user(session, tg_id=5)
    assert await get_role(session, settings, ADMIN_ID) == "owner"
    assert await get_role(session, settings, 7) == "support"
    # support: tickets and user lookup, but no extensions or stats
    assert await svc.user_card(session, settings, 7, 5) is not None
    with pytest.raises(AdminPermissionError):
        await svc.manual_extend(session, settings, 7, 5, 3, "x", NOW)
    with pytest.raises(AdminPermissionError):
        await svc.stats(session, settings, 7, NOW)
    # tech: nodes + devices, no promos
    assert "nodes.manage" in perms_of("tech")
    with pytest.raises(AdminPermissionError):
        await svc.list_promos(session, settings, 8)
    # admin: no staff management, no refunds, no infrastructure management by default
    await svc.stats(session, settings, 9, NOW)
    with pytest.raises(AdminPermissionError):
        await svc.set_staff(session, settings, 9, 10, "admin")
    assert not {"payments.manage", "staff.manage", "nodes.manage"} & perms_of("admin")


async def test_owner_manages_staff(session, settings):
    await svc.set_staff(session, settings, ADMIN_ID, 77, "support")
    assert await get_role(session, settings, 77) == "support"
    with pytest.raises(svc.AdminInputError):
        await svc.set_staff(session, settings, ADMIN_ID, 78, "owner")
    await svc.set_staff(session, settings, ADMIN_ID, 77, None)
    assert await get_role(session, settings, 77) is None
    assert (await session.scalars(select(AuditLog.action))).all() == ["staff_set", "staff_set"]


async def test_admin_manual_extend_is_audited_and_logged_as_bonus(session, settings):
    await make_user(session, tg_id=5)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 5, 10, "компенсация", NOW)
    assert sub.expires_at == NOW + timedelta(days=10)
    log = await session.scalar(select(AuditLog))
    assert log.action == "manual_extend" and log.admin_telegram_id == ADMIN_ID and "компенсация" in log.details
    assert (await session.scalar(select(BonusEntry))).days == 10
    with pytest.raises(svc.AdminInputError):
        await svc.manual_extend(session, settings, ADMIN_ID, 5, 10, "  ", NOW)


async def test_block_suspends_and_unblock_restores(session, settings, vpn):
    user = await make_user(session, tg_id=42)
    out = await trial(session, user, vpn)
    await svc.set_blocked(session, settings, ADMIN_ID, 42, True, NOW, "abuse")
    sub = await session.scalar(select(Subscription))
    assert sub.status == SubStatus.SUSPENDED
    await sync_user_vpn(session, vpn, user, sub, NOW)
    assert not client_of(vpn, 42).enabled and out.device.status == "suspended"
    await svc.set_blocked(session, settings, ADMIN_ID, 42, False, NOW)
    await sync_user_vpn(session, vpn, user, sub, NOW)
    assert sub.status == SubStatus.ACTIVE and client_of(vpn, 42).enabled


async def test_admin_plan_and_promo_management(session, settings):
    plan = await svc.create_plan(session, settings, ADMIN_ID, "duo", 30, 20000, "Duo", device_limit=2)
    assert not plan.is_active and plan.device_limit == 2
    plan = await svc.update_plan(session, settings, ADMIN_ID, "duo", {"price": 19900, "active": True, "devices": 3})
    assert plan.price == 19900 and plan.is_active and plan.device_limit == 3
    promo = await svc.create_promo(session, settings, ADMIN_ID, "new7", "days", 7, ["max=10", "until=2026-12-31"])
    assert promo.code == "NEW7" and promo.max_activations == 10
    promo = await svc.update_promo(session, settings, ADMIN_ID, "NEW7", {"active": False, "plans": "y1, life"})
    assert not promo.is_active and promo.allowed_plans == "y1,life"
    assert (await svc.archive_promo(session, settings, ADMIN_ID, "NEW7")).is_archived
    with pytest.raises(svc.AdminInputError):
        await svc.create_promo(session, settings, ADMIN_ID, "BAD", "percent", 150, [])


async def test_admin_revoke_device_calls_panel(session, settings, vpn):
    user = await make_user(session)
    out = await trial(session, user, vpn)
    await svc.admin_revoke_device(session, settings, vpn, ADMIN_ID, out.device.public_id, "lost phone", NOW)
    assert vpn.clients == {} and out.device.status == "revoked"


# ---------- lifecycle ----------

async def test_expiry_notifications_sent_once(session, vpn):
    user = await make_user(session)
    await trial(session, user, vpn)
    rec = Recorder()
    t = NOW + timedelta(days=2, hours=1)  # 23h left
    assert await send_expiry_notices(session, rec, t, 3, fmt, None) == 1
    assert await send_expiry_notices(session, rec, t + timedelta(minutes=5), 3, fmt, None) == 0
    t2 = NOW + timedelta(days=2, hours=22)  # 2h left → final notice
    assert await send_expiry_notices(session, rec, t2, 3, fmt, None) == 1
    assert await send_expiry_notices(session, rec, t2, 3, fmt, None) == 0
    assert len(rec.sent) == 2


async def test_notifications_respect_user_preference(session, vpn):
    user = await make_user(session)
    await trial(session, user, vpn)
    user.notifications_enabled = False
    await session.commit()
    rec = Recorder()
    await send_expiry_notices(session, rec, NOW + timedelta(days=2, hours=1), 3, fmt, None)
    assert rec.sent == []


async def test_expiry_suspends_all_devices_and_is_idempotent(session, vpn):
    user = await make_user(session)
    await trial(session, user, vpn)
    rec = Recorder()
    later = NOW + timedelta(days=4)
    assert await expire_subscriptions(session, vpn, rec, later, None) == 1
    assert await expire_subscriptions(session, vpn, rec, later, None) == 0
    sub = await session.scalar(select(Subscription))
    assert sub.status == SubStatus.EXPIRED and not client_of(vpn, 42).enabled and not sub.vpn_sync_needed
    assert len(rec.sent) == 1 and "Пробный период закончился" in rec.sent[0][1]


async def test_lifetime_never_expires(session, settings, vpn):
    from app.services.users import extend_subscription

    user = await make_user(session)
    await extend_subscription(session, user, 0, NOW, plan_code="life", plan_title="Lifetime", lifetime=True, device_limit=3)
    await session.commit()
    assert await expire_subscriptions(session, vpn, Recorder(), NOW + timedelta(days=36500), None) == 0
    assert await send_expiry_notices(session, Recorder(), NOW + timedelta(days=36500), 3, fmt, None) == 0


async def test_renewal_after_expiry_restores_access(session, settings, vpn):
    user = await make_user(session)
    await trial(session, user, vpn)
    later = NOW + timedelta(days=5)
    await expire_subscriptions(session, vpn, Recorder(), later, None)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 42, 30, "renew", later)
    assert sub.starts_at == later and sub.expires_at == later + timedelta(days=30)
    assert await sync_user_vpn(session, vpn, user, sub, later)
    assert client_of(vpn, 42).enabled and client_of(vpn, 42).expires_at == sub.expires_at


async def test_vpn_outage_is_retried_by_job(session, settings, vpn):
    user = await make_user(session)
    await trial(session, user, vpn)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 42, 30, "x", NOW)
    vpn.fail_next = 100
    assert await retry_vpn_sync(session, vpn, NOW, max_attempts=5) == (0, 1)
    await session.refresh(sub)
    assert sub.vpn_sync_needed and sub.vpn_sync_attempts == 1
    vpn.fail_next = 0
    assert await retry_vpn_sync(session, vpn, NOW, max_attempts=5) == (1, 0)
    assert client_of(vpn, 42).expires_at == sub.expires_at


async def test_reconcile_recreates_client_deleted_on_panel(session, vpn):
    user = await make_user(session)
    out = await trial(session, user, vpn)
    old_id = out.device.external_id
    vpn.clients.clear()
    assert await reconcile_vpn(session, vpn, NOW + timedelta(hours=1)) == 1
    await session.refresh(out.device)
    assert client_of(vpn, 42) is not None and out.device.external_id != old_id

from datetime import timedelta

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select

from app.models import AuditLog, Subscription, SubStatus
from app.services import admin as svc
from app.services.ops import expire_subscriptions, reconcile_vpn, retry_vpn_sync, send_expiry_notices
from app.services.trial import activate_trial
from app.services.vpn_sync import sync_user_vpn
from app.vpn.base import VPNUnavailableError
from app.vpn.dev_providers import TestVPNProvider
from app.vpn.retry import with_retries
from tests.conftest import ADMIN_ID, LEGAL, NOW, make_user


class Recorder:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def __call__(self, tg: int, text: str, kb: InlineKeyboardMarkup | None) -> bool:
        self.sent.append((tg, text))
        return True


fmt = lambda dt: dt.isoformat()  # noqa: E731


# ---------- admin access ----------

async def test_non_admin_is_denied_everywhere(session, settings):
    await make_user(session, tg_id=5)
    with pytest.raises(svc.AdminPermissionError):
        await svc.stats(session, settings, 5, NOW)
    with pytest.raises(svc.AdminPermissionError):
        await svc.manual_extend(session, settings, 5, 5, 30, "hack", NOW)
    with pytest.raises(svc.AdminPermissionError):
        await svc.create_promo(session, settings, 5, "FREE999", "days", 999, [])
    with pytest.raises(svc.AdminPermissionError):
        await svc.change_setting(session, settings, 5, "maintenance", "on")
    assert await session.scalar(select(Subscription)) is None


async def test_admin_manual_extend_is_audited(session, settings):
    await make_user(session, tg_id=5)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 5, 10, "компенсация", NOW)
    assert sub.expires_at == NOW + timedelta(days=10)
    log = await session.scalar(select(AuditLog))
    assert log.action == "manual_extend" and log.admin_telegram_id == ADMIN_ID and "компенсация" in log.details
    with pytest.raises(svc.AdminInputError):
        await svc.manual_extend(session, settings, ADMIN_ID, 5, 10, "  ", NOW)


async def test_admin_plan_and_promo_management(session, settings):
    plan = await svc.create_plan(session, settings, ADMIN_ID, "m1", 30, 29900, "1 месяц")
    assert not plan.is_active
    plan = await svc.update_plan(session, settings, ADMIN_ID, "m1", ["price=19900", "active=on"])
    assert plan.price == 19900 and plan.is_active
    promo = await svc.create_promo(session, settings, ADMIN_ID, "new7", "days", 7, ["max=10", "until=2026-12-31"])
    assert promo.code == "NEW7" and promo.max_activations == 10
    promo = await svc.update_promo(session, settings, ADMIN_ID, "NEW7", ["active=off"])
    assert not promo.is_active
    promo = await svc.archive_promo(session, settings, ADMIN_ID, "NEW7")
    assert promo.is_archived
    with pytest.raises(svc.AdminInputError):
        await svc.create_promo(session, settings, ADMIN_ID, "BAD", "percent", 150, [])


def test_admin_router_filter_uses_admin_ids(settings):
    assert settings.is_admin(ADMIN_ID) and not settings.is_admin(5) and not settings.is_admin(None)


# ---------- lifecycle ----------

async def test_expiry_notifications_sent_once(session, vpn):
    user = await make_user(session)
    await activate_trial(session, user, vpn, LEGAL, NOW)
    rec = Recorder()
    t = NOW + timedelta(days=2, hours=1)  # 23h left
    assert await send_expiry_notices(session, rec, t, 3, fmt, None) == 1
    assert await send_expiry_notices(session, rec, t + timedelta(minutes=5), 3, fmt, None) == 0
    t2 = NOW + timedelta(days=2, hours=22)  # 2h left → final notice
    assert await send_expiry_notices(session, rec, t2, 3, fmt, None) == 1
    assert await send_expiry_notices(session, rec, t2, 3, fmt, None) == 0
    assert len(rec.sent) == 2


async def test_expiry_suspends_access_and_is_idempotent(session, vpn):
    user = await make_user(session)
    await activate_trial(session, user, vpn, LEGAL, NOW)
    rec = Recorder()
    later = NOW + timedelta(days=4)
    assert await expire_subscriptions(session, vpn, rec, later, None) == 1
    assert await expire_subscriptions(session, vpn, rec, later, None) == 0
    sub = await session.scalar(select(Subscription))
    assert sub.status == SubStatus.EXPIRED and not vpn.clients[42].enabled and not sub.vpn_sync_needed
    assert len(rec.sent) == 1 and "Пробный период закончился" in rec.sent[0][1]


async def test_renewal_after_expiry_restores_access(session, settings, vpn):
    user = await make_user(session)
    await activate_trial(session, user, vpn, LEGAL, NOW)
    later = NOW + timedelta(days=5)
    await expire_subscriptions(session, vpn, Recorder(), later, None)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 42, 30, "renew", later)
    assert sub.starts_at == later and sub.expires_at == later + timedelta(days=30)
    assert await sync_user_vpn(session, vpn, user, sub, later)
    assert vpn.clients[42].enabled


# ---------- VPN provider errors ----------

async def test_retry_gives_up_after_limit():
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise VPNUnavailableError

    with pytest.raises(VPNUnavailableError):
        await with_retries(op, attempts=3, base_delay=0)
    assert calls == 3


async def test_vpn_outage_is_retried_by_job(session, settings, vpn):
    await make_user(session)
    sub = await svc.manual_extend(session, settings, ADMIN_ID, 42, 30, "x", NOW)
    vpn.fail_next = 100
    ok, failed = await retry_vpn_sync(session, vpn, NOW, max_attempts=5, vpn_attempts=1)
    assert (ok, failed) == (0, 1)
    await session.refresh(sub)
    assert sub.vpn_sync_needed and sub.vpn_sync_attempts == 1 and sub.vpn_last_error == "VPNUnavailableError"
    vpn.fail_next = 0
    assert await retry_vpn_sync(session, vpn, NOW, max_attempts=5, vpn_attempts=1) == (1, 0)
    assert vpn.clients[42].enabled


async def test_reconcile_detects_drift(session, vpn):
    user = await make_user(session)
    await activate_trial(session, user, vpn, LEGAL, NOW)
    vpn.clients.clear()  # client deleted on the server behind our back
    assert await reconcile_vpn(session, vpn, NOW + timedelta(hours=1)) == 1
    assert await retry_vpn_sync(session, vpn, NOW + timedelta(hours=1), 5, 1) == (1, 0)
    assert 42 in vpn.clients


async def test_test_vpn_config_is_labelled():
    vpn = TestVPNProvider()
    await vpn.ensure_client(1, NOW + timedelta(days=1))
    conn = await vpn.get_connection(1)
    assert conn.is_test and "НЕ РАБОЧИЙ VPN" in conn.config_text

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Subscription, SubStatus, TrialActivation, User
from app.services import settings_store
from app.services.trial import TrialResult, activate_trial
from app.services.users import compute_period, register_user
from app.vpn.mock import NotConfiguredVPNService
from tests.conftest import LEGAL, NOW, client_of, make_user


async def test_registration_and_repeat_start_keeps_data(session):
    user, created = await register_user(session, 77, "a", "A", NOW)
    assert created
    session.add(Subscription(user_id=user.id, starts_at=NOW, expires_at=NOW + timedelta(days=5)))
    await session.commit()
    again, created2 = await register_user(session, 77, "b", "B", NOW + timedelta(hours=1))
    assert not created2 and again.id == user.id and again.username == "b"
    assert await session.scalar(select(func.count(User.id))) == 1
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert sub.expires_at == NOW + timedelta(days=5)


async def test_trial_activation_creates_access(session, vpn):
    user = await make_user(session)
    out = await activate_trial(session, user, vpn, LEGAL, NOW)
    assert out.result == TrialResult.ACTIVATED
    sub = out.subscription
    assert sub.is_trial and sub.starts_at == NOW and sub.expires_at == NOW + timedelta(days=3)
    assert not sub.vpn_sync_needed and sub.device_limit == 1
    assert out.device is not None and out.device.status == "active"
    assert client_of(vpn, 42).enabled and client_of(vpn, 42).expires_at == sub.expires_at


async def test_trial_cannot_be_taken_twice(session, vpn):
    user = await make_user(session)
    assert (await activate_trial(session, user, vpn, LEGAL, NOW)).result == TrialResult.ACTIVATED
    out = await activate_trial(session, user, vpn, LEGAL, NOW + timedelta(days=10))
    assert out.result == TrialResult.ALREADY_USED
    assert await session.scalar(select(func.count(TrialActivation.id))) == 1


async def test_trial_unique_constraint_at_db_level(session):
    user = await make_user(session)
    session.add(TrialActivation(user_id=user.id, expires_at=NOW))
    await session.commit()
    session.add(TrialActivation(user_id=user.id, expires_at=NOW))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_trial_requires_terms_acceptance(session, vpn):
    user = await make_user(session, accepted=False)
    assert (await activate_trial(session, user, vpn, LEGAL, NOW)).result == TrialResult.NEED_TERMS


async def test_trial_vpn_unavailable_does_not_spend_trial(session, vpn):
    user = await make_user(session)
    user_id = user.id
    vpn.fail_next = 10
    out = await activate_trial(session, user, vpn, LEGAL, NOW)
    assert out.result == TrialResult.VPN_UNAVAILABLE and out.subscription is None
    assert vpn.clients == {}
    assert await session.scalar(select(func.count(TrialActivation.id))) == 0
    assert await session.scalar(select(func.count(Subscription.id))) == 0
    vpn.fail_next = 0
    user = await session.get(User, user_id)
    assert (await activate_trial(session, user, vpn, LEGAL, NOW)).result == TrialResult.ACTIVATED


async def test_trial_not_offered_without_vpn_infrastructure(session):
    user = await make_user(session)
    assert (await activate_trial(session, user, NotConfiguredVPNService(), LEGAL, NOW)).result == TrialResult.SERVICE_NOT_READY
    assert await session.scalar(select(func.count(TrialActivation.id))) == 0


async def test_trial_disabled_by_admin_setting(session, vpn):
    user = await make_user(session)
    await settings_store.set_setting(session, "trial_enabled", "off")
    await session.commit()
    assert (await activate_trial(session, user, vpn, LEGAL, NOW)).result == TrialResult.DISABLED


async def test_trial_recovers_lost_create_response(session, vpn):
    """Panel created the client but the response was lost: the bot finds it by username instead of failing."""
    user = await make_user(session)
    vpn.apply_then_fail = True
    out = await activate_trial(session, user, vpn, LEGAL, NOW)
    assert out.result == TrialResult.ACTIVATED
    assert len(vpn.clients) == 1 and ("find", out.device.external_username) in vpn.calls


def test_compute_period_lifetime():
    from app.models import LIFETIME_UNTIL

    active = Subscription(status=SubStatus.ACTIVE, starts_at=NOW, expires_at=NOW + timedelta(days=2), is_lifetime=False)
    assert compute_period(active, NOW, 0, lifetime=True) == (NOW, LIFETIME_UNTIL)
    life = Subscription(status=SubStatus.ACTIVE, starts_at=NOW, expires_at=LIFETIME_UNTIL, is_lifetime=True)
    assert compute_period(life, NOW + timedelta(days=5000), 30)[1] == LIFETIME_UNTIL


def test_compute_period_sequential_and_after_expiry():
    active = Subscription(status=SubStatus.ACTIVE, starts_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=2),
                          is_lifetime=False)
    assert compute_period(active, NOW, 30)[1] == NOW + timedelta(days=32)
    expired = Subscription(status=SubStatus.ACTIVE, starts_at=NOW - timedelta(days=40), expires_at=NOW - timedelta(days=1),
                           is_lifetime=False)
    assert compute_period(expired, NOW, 30) == (NOW, NOW + timedelta(days=30))
    assert compute_period(None, NOW, 3) == (NOW, NOW + timedelta(days=3))
    with pytest.raises(ValueError):
        compute_period(None, NOW, 0)

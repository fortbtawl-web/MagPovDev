from datetime import timedelta

from sqlalchemy import func, select

from app.models import BonusEntry, Subscription, User
from app.payments.providers import PaymentEventData
from app.services import admin as svc
from app.services import referrals as refs
from app.services import settings_store
from app.services.orders import PaymentOutcome, create_order, process_payment_event
from app.services.users import get_subscription, register_user
from tests.conftest import ADMIN_ID, NOW, make_plan, make_user, trial


def ev(order, event_id):
    return PaymentEventData("test", event_id, order.id, True, order.amount, order.currency, True)


async def test_referral_link_and_payload():
    assert refs.referral_link("SpiritBot", 42) == "https://t.me/SpiritBot?start=ref_42"
    assert refs.referral_link(None, 42) is None
    assert refs.parse_ref_payload("ref_42") == 42
    assert refs.parse_ref_payload("ref_abc") is None and refs.parse_ref_payload("promo") is None
    assert "t.me/share/url?url=https%3A%2F%2Ft.me" in refs.share_url("https://t.me/x?start=ref_1")


async def test_registration_binds_referrer_once(session):
    referrer = await make_user(session, tg_id=1)
    friend, created = await register_user(session, 2, None, "Аня", NOW, 1)
    assert created and friend.referrer_id == referrer.id
    other = await make_user(session, tg_id=3)
    again, created = await register_user(session, 2, None, "Аня", NOW, 3)  # repeated /start with another link
    assert not created and again.referrer_id == referrer.id != other.id


async def test_self_invite_and_unknown_referrer_ignored(session):
    me, _ = await register_user(session, 5, None, None, NOW, 5)
    assert me.referrer_id is None
    ghost, _ = await register_user(session, 6, None, None, NOW, 999)
    assert ghost.referrer_id is None


async def test_trial_bonus_granted_once(session, vpn):
    referrer = await make_user(session, tg_id=1)
    friend = await make_user(session, tg_id=2, referrer=1)
    out = await trial(session, friend, vpn)
    assert out.reward and out.reward.days == 3 and out.reward.referrer_telegram_id == 1
    sub = await get_subscription(session, referrer.id)
    assert sub.expires_at == NOW + timedelta(days=3)
    await trial(session, friend, vpn)  # second attempt is refused and grants nothing
    assert await session.scalar(select(func.count(BonusEntry.id))) == 1


async def test_payment_bonus_once_even_with_duplicate_webhooks_and_second_payment(session):
    referrer = await make_user(session, tg_id=1)
    friend = await make_user(session, tg_id=2, referrer=1)
    await make_plan(session)
    o1 = await create_order(session, friend, "m1", "test", True, NOW)
    r1 = await process_payment_event(session, ev(o1, "e1"), NOW)
    assert r1.outcome == PaymentOutcome.APPLIED and r1.referral.days == 7
    assert (await process_payment_event(session, ev(o1, "e1"), NOW)).outcome == PaymentOutcome.DUPLICATE
    o2 = await create_order(session, friend, "m1", "test", True, NOW)
    r2 = await process_payment_event(session, ev(o2, "e2"), NOW)
    assert r2.referral is None
    total = await session.scalar(select(func.sum(BonusEntry.days)).where(BonusEntry.user_id == referrer.id))
    assert total == 7
    assert (await get_subscription(session, referrer.id)).expires_at == NOW + timedelta(days=7)


async def test_program_disabled_and_custom_amounts(session, vpn):
    await make_user(session, tg_id=1)
    friend = await make_user(session, tg_id=2, referrer=1)
    await settings_store.set_setting(session, "referral_enabled", "off")
    await session.commit()
    assert (await trial(session, friend, vpn)).reward is None
    await settings_store.set_setting(session, "referral_enabled", "on")
    await settings_store.set_setting(session, "referral_payment_days", "14")
    await session.commit()
    await make_plan(session)
    order = await create_order(session, friend, "m1", "test", True, NOW)
    assert (await process_payment_event(session, ev(order, "x"), NOW)).referral.days == 14


async def test_summary_and_invites_hide_private_data(session, vpn):
    referrer = await make_user(session, tg_id=1)
    friend, _ = await register_user(session, 222333, "secret_name", "Борис", NOW, 1)
    from app.services.users import accept_terms

    await accept_terms(session, friend, "test-v1", NOW)
    await trial(session, friend, vpn)
    s = await refs.summary(session, referrer)
    assert (s.invited, s.bonus_days, s.pending) == (1, 3, 1)
    [inv] = await refs.invites(session, referrer)
    assert inv.name == "Б•••" and inv.trial_done and not inv.payment_done and inv.bonus_days == 3
    assert "222333" not in repr(inv) and "secret_name" not in repr(inv)


async def test_manual_correction_requires_reason_and_is_audited(session, settings):
    await make_user(session, tg_id=1)
    import pytest

    with pytest.raises(svc.AdminInputError):
        await svc.referral_correction(session, settings, ADMIN_ID, 1, 5, "", NOW)
    await svc.referral_correction(session, settings, ADMIN_ID, 1, 5, "ошибка начисления", NOW)
    await svc.referral_correction(session, settings, ADMIN_ID, 1, -2, "откат", NOW)
    sub = await session.scalar(select(Subscription))
    assert sub.expires_at == NOW + timedelta(days=3)
    stats, history = await svc.referral_overview(session, settings, ADMIN_ID)
    assert len(history) == 2 and stats["invites"] == 0
    assert await session.scalar(select(func.count(User.id))) == 1

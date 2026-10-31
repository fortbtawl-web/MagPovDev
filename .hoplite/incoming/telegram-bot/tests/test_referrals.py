"""Registration and referral program."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from backend.models import BonusTransaction, Referral, ReferralReward, User
from backend.services import payments as pay_svc
from backend.services import referrals as ref_svc
from backend.services import trial as trial_svc
from backend.services.context import ServiceError
from tests.conftest import make_user


async def count(ctx, model, *where):
    async with ctx.session() as s:
        return await s.scalar(select(func.count()).select_from(model).where(*where))


async def test_new_user_created_with_unique_code(ctx):
    reg = await make_user(ctx, 1)
    reg2 = await make_user(ctx, 2)
    assert reg.created and reg2.created
    assert reg.user.referral_code and reg.user.referral_code != reg2.user.referral_code


async def test_repeat_start_no_duplicate(ctx):
    await make_user(ctx, 1, name="Old")
    reg = await make_user(ctx, 1, name="New")
    assert not reg.created
    assert await count(ctx, User) == 1
    assert reg.user.first_name == "New"


async def test_valid_referral_link(ctx):
    inviter = await make_user(ctx, 10)
    reg = await make_user(ctx, 11, f"ref_{inviter.user.referral_code}")
    assert reg.referrer_id == 10 and reg.referral_note == "ok"
    # Numeric ref_<telegram id> links are accepted too.
    reg2 = await make_user(ctx, 12, "ref_10")
    assert reg2.referrer_id == 10


async def test_self_invite_rejected(ctx):
    reg = await make_user(ctx, 20, "ref_20")
    assert reg.referral_note == "self"
    assert await count(ctx, Referral) == 0


async def test_invalid_referral_ignored(ctx):
    reg = await make_user(ctx, 21, "ref_doesnotexist")
    assert reg.referral_note == "invalid" and await count(ctx, Referral) == 0
    reg = await make_user(ctx, 22, "ref_../../x")
    assert reg.referral_note is None


async def test_repeat_registration_and_referrer_change_blocked(ctx):
    a = await make_user(ctx, 30)
    b = await make_user(ctx, 31)
    await make_user(ctx, 32, f"ref_{a.user.referral_code}")
    again = await make_user(ctx, 32, f"ref_{b.user.referral_code}")
    assert not again.created and again.referrer_id is None
    async with ctx.session() as s:
        ref = await s.scalar(select(Referral).where(Referral.referred_id == 32))
    assert ref.referrer_id == 30
    assert await count(ctx, Referral) == 1


async def test_existing_user_cannot_be_attached_later(ctx):
    a = await make_user(ctx, 33)
    await make_user(ctx, 34)  # registered without referral
    await make_user(ctx, 34, f"ref_{a.user.referral_code}")
    assert await count(ctx, Referral) == 0


async def test_trial_bonus_granted_once(ctx):
    a = await make_user(ctx, 40)
    await make_user(ctx, 41, f"ref_{a.user.referral_code}")
    # Registration alone gives nothing.
    assert await count(ctx, ReferralReward) == 0
    res = await trial_svc.activate_trial(ctx, 41)
    assert res.confirmed
    async with ctx.session() as s:
        assert await ref_svc.bonus_balance(s, 40) == 3
    with pytest.raises(ServiceError):
        await trial_svc.activate_trial(ctx, 41)
    async with ctx.session() as s:
        assert await ref_svc.bonus_balance(s, 40) == 3
        ref = await s.scalar(select(Referral).where(Referral.referred_id == 41))
        assert ref.trial_activated_at is not None


async def test_trial_bonus_not_granted_when_vpn_fails(ctx):
    from backend.integrations.vpn import VpnError

    a = await make_user(ctx, 42)
    await make_user(ctx, 43, f"ref_{a.user.referral_code}")
    for n in ctx.extra["nodes"].values():
        n.fail["create"] = VpnError("down", code="unreachable", retryable=True)
    with pytest.raises(ServiceError):
        await trial_svc.activate_trial(ctx, 43)
    assert await count(ctx, ReferralReward) == 0
    # Trial not consumed: works once the server is back.
    for n in ctx.extra["nodes"].values():
        n.fail.clear()
    assert (await trial_svc.activate_trial(ctx, 43)).confirmed
    assert await count(ctx, ReferralReward) == 1


async def _pay(ctx, uid, plan="dev1_30"):
    order, pay = await pay_svc.create_order(ctx, uid, plan)
    ctx.payments.settle(pay.provider_payment_id)
    state = await ctx.payments.get_payment(pay.provider_payment_id)
    return order, pay, await pay_svc.handle_state(ctx, "fake", state, source="webhook")


async def test_first_payment_bonus_once(ctx):
    a = await make_user(ctx, 50)
    await make_user(ctx, 51, f"ref_{a.user.referral_code}")
    _, pay, result = await _pay(ctx, 51)
    assert result == "paid"
    async with ctx.session() as s:
        assert await ref_svc.bonus_balance(s, 50) == 7
    # Second purchase: no new bonus.
    _, _, result2 = await _pay(ctx, 51, "m3")
    assert result2 == "paid"
    async with ctx.session() as s:
        assert await ref_svc.bonus_balance(s, 50) == 7
    assert await count(ctx, ReferralReward, ReferralReward.event_type == "first_payment") == 1


async def test_duplicate_webhook_no_double_bonus(ctx):
    a = await make_user(ctx, 60)
    await make_user(ctx, 61, f"ref_{a.user.referral_code}")
    _, pay, _ = await _pay(ctx, 61)
    state = await ctx.payments.get_payment(pay.provider_payment_id)
    assert await pay_svc.handle_state(ctx, "fake", state, source="webhook") == "duplicate"
    assert await count(ctx, BonusTransaction) == 1


async def test_concurrent_events_single_reward(ctx):
    a = await make_user(ctx, 70)
    await make_user(ctx, 71, f"ref_{a.user.referral_code}")
    order, pay = await pay_svc.create_order(ctx, 71, "dev1_30")
    ctx.payments.settle(pay.provider_payment_id)
    state = await ctx.payments.get_payment(pay.provider_payment_id)
    results = await asyncio.gather(*[pay_svc.handle_state(ctx, "fake", state, source="webhook") for _ in range(6)])
    assert results.count("paid") == 1 and results.count("duplicate") == 5
    assert await count(ctx, ReferralReward) == 1
    assert await count(ctx, BonusTransaction) == 1


async def test_referral_disabled_no_reward(ctx):
    from backend.services import admin as admin_svc
    from tests.conftest import ADMIN_ID

    await admin_svc.update_setting(ctx, ADMIN_ID, "web", "referral_enabled", False, "пауза программы")
    a = await make_user(ctx, 80)
    await make_user(ctx, 81, f"ref_{a.user.referral_code}")
    await trial_svc.activate_trial(ctx, 81)
    assert await count(ctx, ReferralReward) == 0


async def test_referral_summary_and_privacy(ctx):
    a = await make_user(ctx, 90)
    await make_user(ctx, 91, f"ref_{a.user.referral_code}", name="Мария")
    await trial_svc.activate_trial(ctx, 91)
    async with ctx.session() as s:
        summary = await ref_svc.referral_summary(s, 90)
        invites = await ref_svc.list_invites(s, 90)
    assert summary == {"invited": 1, "with_trial": 1, "with_payment": 0, "earned_days": 3, "pending": 1, "balance_days": 3}
    assert invites[0]["name"].startswith("М") and "Мария" not in invites[0]["name"]
    assert invites[0]["trial"] and not invites[0]["paid"] and invites[0]["bonus_days"] == 3

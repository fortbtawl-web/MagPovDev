"""Orders, payments, subscriptions."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from backend.integrations.payments import WebhookRejected
from backend.integrations.vpn import VpnError
from backend.models import Device, Order, Subscription, SubscriptionEvent
from backend.services import account as account_svc
from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import ServiceError
from backend.utils import utcnow
from tests.conftest import make_user


async def pay(ctx, uid, plan="dev1_30", *, amount=None, status="succeeded"):
    order, p = await pay_svc.create_order(ctx, uid, plan)
    ctx.payments.settle(p.provider_payment_id, status, amount=amount)
    state = await ctx.payments.get_payment(p.provider_payment_id)
    return order, p, await pay_svc.handle_state(ctx, "fake", state, source="webhook")


async def current(ctx, uid):
    async with ctx.session() as s:
        return await subs_svc.get_current(s, uid)


async def test_order_does_not_grant_access(ctx):
    await make_user(ctx, 1)
    order, p = await pay_svc.create_order(ctx, 1, "dev1_30")
    assert order.status == "pending" and p.confirmation_url
    assert await current(ctx, 1) is None
    # Checking status while unpaid keeps it unpaid.
    refreshed = await pay_svc.refresh_order(ctx, 1, order.id)
    assert refreshed.status == "pending" and await current(ctx, 1) is None


async def test_successful_payment_activates_and_provisions(ctx):
    await make_user(ctx, 2)
    order, p, result = await pay(ctx, 2)
    assert result == "paid"
    sub = await current(ctx, 2)
    assert sub.status == "active" and sub.device_limit == 1
    assert abs((sub.expires_at - utcnow()) - timedelta(days=30)) < timedelta(minutes=1)
    async with ctx.session() as s:
        o = await s.get(Order, order.id)
        devices = await dev_svc.list_devices(s, 2)
    assert o.fulfillment_status == "done"
    assert len(devices) == 1 and devices[0].status == "active"
    node = ctx.extra["nodes"][devices[0].node.code]
    assert devices[0].client_name in node.clients
    assert any("Оплата подтверждена" in t for _, t in ctx.notifier.sent)


async def test_webhook_bad_signature_rejected(ctx):
    await make_user(ctx, 3)
    _, p = await pay_svc.create_order(ctx, 3, "dev1_30")
    ctx.payments.settle(p.provider_payment_id)
    body = json.dumps({"event": "payment.succeeded", "object": {"id": p.provider_payment_id}}).encode()
    with pytest.raises(WebhookRejected):
        await pay_svc.handle_webhook(ctx, body=body, headers={"x-test-signature": "forged"}, client_ip="1.2.3.4")
    assert await current(ctx, 3) is None
    assert await pay_svc.handle_webhook(ctx, body=body, headers={"x-test-signature": "valid"}, client_ip="1.2.3.4") == "paid"


async def test_webhook_body_not_trusted(ctx):
    """A webhook claiming success for an unpaid payment changes nothing: status is re-read from the provider."""
    await make_user(ctx, 4)
    _, p = await pay_svc.create_order(ctx, 4, "dev1_30")
    body = json.dumps({"event": "payment.succeeded", "object": {"id": p.provider_payment_id, "status": "succeeded"}}).encode()
    assert await pay_svc.handle_webhook(ctx, body=body, headers={"x-test-signature": "valid"}, client_ip=None) == "pending"
    assert await current(ctx, 4) is None


async def test_wrong_amount_rejected(ctx):
    await make_user(ctx, 5)
    order, _, result = await pay(ctx, 5, amount=100)
    assert result == "mismatch"
    assert await current(ctx, 5) is None
    async with ctx.session() as s:
        assert (await s.get(Order, order.id)).status == "pending"


async def test_canceled_payment(ctx):
    await make_user(ctx, 6)
    order, _, result = await pay(ctx, 6, status="canceled")
    assert result == "canceled"
    async with ctx.session() as s:
        assert (await s.get(Order, order.id)).status == "canceled"
    assert await current(ctx, 6) is None


async def test_repeated_processing_applies_once(ctx):
    await make_user(ctx, 7)
    order, p, _ = await pay(ctx, 7)
    state = await ctx.payments.get_payment(p.provider_payment_id)
    for _ in range(3):
        assert await pay_svc.handle_state(ctx, "fake", state, source="poll") == "duplicate"
    sub = await current(ctx, 7)
    assert abs((sub.expires_at - utcnow()) - timedelta(days=30)) < timedelta(minutes=1)
    async with ctx.session() as s:
        assert await s.scalar(select(func.count(SubscriptionEvent.id)).where(SubscriptionEvent.order_id == order.id)) == 1


async def test_extend_active_subscription(ctx):
    await make_user(ctx, 8)
    await pay(ctx, 8)
    first = (await current(ctx, 8)).expires_at
    await pay(ctx, 8, "m3")
    sub = await current(ctx, 8)
    assert sub.expires_at - first == timedelta(days=90)
    # Node expiry follows the subscription.
    async with ctx.session() as s:
        d = (await dev_svc.list_devices(s, 8))[0]
    assert ctx.extra["nodes"][d.node.code].clients[d.client_name]["expires_at"] == sub.expires_at


async def test_reactivation_after_expiry(ctx):
    await make_user(ctx, 9)
    await pay(ctx, 9)
    async with ctx.session() as s, s.begin():
        await s.execute(update(Subscription).where(Subscription.user_id == 9).values(expires_at=utcnow() - timedelta(days=5)))
    async with ctx.session() as s, s.begin():
        assert len(await subs_svc.expire_due(s)) == 1
    assert (await current(ctx, 9)).status == "expired"
    await pay(ctx, 9)
    sub = await current(ctx, 9)
    assert sub.status == "active"
    assert abs((sub.expires_at - utcnow()) - timedelta(days=30)) < timedelta(minutes=1)
    async with ctx.session() as s:
        devices = await dev_svc.list_devices(s, 9)
    assert [d.status for d in devices] == ["active"]


async def test_lifetime(ctx):
    await make_user(ctx, 10)
    await pay(ctx, 10, "lifetime")
    sub = await current(ctx, 10)
    assert sub.is_lifetime and sub.expires_at is None and sub.status == "active"
    async with ctx.session() as s, s.begin():
        assert await subs_svc.expire_due(s) == []
    assert (await current(ctx, 10)).status == "active"
    with pytest.raises(ServiceError):
        await pay_svc.create_order(ctx, 10, "dev1_30")
    # The node still needs a concrete expiry: a technical horizon that the worker keeps pushing forward.
    async with ctx.session() as s:
        d = (await dev_svc.list_devices(s, 10))[0]
    node_exp = ctx.extra["nodes"][d.node.code].clients[d.client_name]["expires_at"]
    assert node_exp - utcnow() > timedelta(days=360)


async def test_vpn_error_after_payment_then_retry(ctx):
    await make_user(ctx, 11)
    for n in ctx.extra["nodes"].values():
        n.fail["create"] = VpnError("timeout", code="timeout", retryable=True)
    order, _, result = await pay(ctx, 11)
    assert result == "paid"
    sub = await current(ctx, 11)
    assert sub.status == "active"  # payment applied
    async with ctx.session() as s:
        o = await s.get(Order, order.id)
        devs = await dev_svc.list_devices(s, 11)
    assert o.fulfillment_status == "vpn_failed"
    assert devs[0].status == "pending"
    assert any("не подтвердил" in t for _, t in ctx.notifier.sent)
    # Server back: retry worker confirms creation, fulfillment completes.
    for n in ctx.extra["nodes"].values():
        n.fail.clear()
    async with ctx.session() as s, s.begin():
        from backend.models import VpnOperation

        await s.execute(update(VpnOperation).values(next_retry_at=utcnow()))
    await dev_svc.retry_pending(ctx)
    assert await pay_svc.fulfill_order(ctx, order.id)
    async with ctx.session() as s:
        assert (await s.get(Order, order.id)).fulfillment_status == "done"
        assert (await dev_svc.list_devices(s, 11))[0].status == "active"


async def test_repeated_fulfillment_creates_single_device(ctx):
    await make_user(ctx, 12)
    order, _, _ = await pay(ctx, 12)
    async with ctx.session() as s, s.begin():
        await s.execute(update(Order).where(Order.id == order.id).values(fulfillment_status="vpn_failed"))
    await pay_svc.fulfill_order(ctx, order.id)
    await pay_svc.fulfill_order(ctx, order.id)
    async with ctx.session() as s:
        assert await s.scalar(select(func.count(Device.id)).where(Device.user_id == 12)) == 1


async def test_too_many_devices_for_smaller_plan(ctx):
    await make_user(ctx, 13)
    await pay(ctx, 13, "dev2_30")
    await dev_svc.add_device(ctx, 13)
    with pytest.raises(ServiceError) as e:
        await pay_svc.create_order(ctx, 13, "dev1_30")
    assert e.value.code == "too_many_devices"


async def test_payments_unavailable_without_provider(ctx):
    await make_user(ctx, 14)
    ctx.payments = None
    with pytest.raises(ServiceError) as e:
        await pay_svc.create_order(ctx, 14, "dev1_30")
    assert e.value.code == "payments_disabled"


async def test_apply_bonus_days(ctx):
    from backend.services import admin as admin_svc
    from tests.conftest import ADMIN_ID

    await make_user(ctx, 15)
    await admin_svc.adjust_bonus(ctx, ADMIN_ID, "web", 15, 5, "компенсация")
    sub = await account_svc.apply_bonus_days(ctx, 15)
    assert sub.source == "bonus" and abs((sub.expires_at - utcnow()) - timedelta(days=5)) < timedelta(minutes=1)
    with pytest.raises(ServiceError):
        await account_svc.apply_bonus_days(ctx, 15)

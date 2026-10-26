import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Order, OrderStatus, PaymentEvent, PromoCode, PromoKind, Subscription
from app.payments.providers import InvalidWebhookError, PaymentEventData, TestPaymentProvider
from app.services.orders import (
    PaymentOutcome,
    cancel_stale_orders,
    create_order,
    process_payment_event,
    validate_for_checkout,
)
from app.services.promos import activate_promo
from tests.conftest import NOW, make_plan, make_user

provider = TestPaymentProvider("s3cret", "https://example.test")


def ev(order: Order, success=True, event_id="evt-1", amount=None, is_test=True, prov="test") -> PaymentEventData:
    return PaymentEventData(prov, event_id, order.id, success, order.amount if amount is None else amount, order.currency, is_test)


async def test_order_uses_server_side_price(session):
    user = await make_user(session)
    await make_plan(session, price=29900)
    order = await create_order(session, user, "m1", "test", True, NOW)
    assert order.amount == 29900 and order.duration_days == 30 and order.status == OrderStatus.CREATED
    assert await create_order(session, user, "nonexistent", "test", True, NOW) is None


async def test_disabled_plan_cannot_be_ordered(session):
    user = await make_user(session)
    plan = await make_plan(session)
    plan.is_active = False
    await session.commit()
    assert await create_order(session, user, "m1", "test", True, NOW) is None


async def test_payment_confirmation_activates_subscription(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "test", True, NOW)
    res = await process_payment_event(session, ev(order), NOW)
    assert res.outcome == PaymentOutcome.APPLIED
    assert res.order.status == OrderStatus.PAID and res.order.paid_at == NOW
    assert res.subscription.expires_at == NOW + timedelta(days=30)


async def test_duplicate_webhook_does_not_extend_twice(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "test", True, NOW)
    assert (await process_payment_event(session, ev(order), NOW)).outcome == PaymentOutcome.APPLIED
    assert (await process_payment_event(session, ev(order), NOW)).outcome == PaymentOutcome.DUPLICATE
    # a different event id for an already-paid order is also ignored
    assert (await process_payment_event(session, ev(order, event_id="evt-2"), NOW)).outcome == PaymentOutcome.DUPLICATE
    sub = await session.scalar(select(Subscription))
    assert sub.expires_at == NOW + timedelta(days=30)
    assert await session.scalar(select(func.count(PaymentEvent.id))) == 2


async def test_declined_payment_marks_failed_without_access(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "test", True, NOW)
    res = await process_payment_event(session, ev(order, success=False), NOW)
    assert res.outcome == PaymentOutcome.FAILED and res.order.status == OrderStatus.FAILED
    assert await session.scalar(select(func.count(Subscription.id))) == 0


async def test_amount_mismatch_is_rejected(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "test", True, NOW)
    res = await process_payment_event(session, ev(order, amount=1), NOW)
    assert res.outcome == PaymentOutcome.MISMATCH
    assert (await session.get(Order, order.id)).status != OrderStatus.PAID


async def test_test_event_cannot_pay_real_order(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "telegram", False, NOW)
    res = await process_payment_event(session, ev(order, is_test=True, prov="test"), NOW)
    assert res.outcome == PaymentOutcome.MISMATCH


async def test_renewal_extends_from_current_end(session):
    user = await make_user(session)
    await make_plan(session)
    o1 = await create_order(session, user, "m1", "test", True, NOW)
    await process_payment_event(session, ev(o1, event_id="a"), NOW)
    o2 = await create_order(session, user, "m1", "test", True, NOW + timedelta(days=5))
    res = await process_payment_event(session, ev(o2, event_id="b"), NOW + timedelta(days=5))
    assert res.subscription.expires_at == NOW + timedelta(days=60)


async def test_payment_after_restart_on_cancelled_order_is_honoured(session):
    """Order was cancelled by the TTL job, but the provider later confirms money: access must be granted once."""
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "test", True, NOW)
    assert await cancel_stale_orders(session, 24, NOW + timedelta(days=2)) == 1
    res = await process_payment_event(session, ev(order), NOW + timedelta(days=2))
    assert res.outcome == PaymentOutcome.APPLIED


async def test_pre_checkout_validation(session):
    user = await make_user(session)
    await make_plan(session)
    order = await create_order(session, user, "m1", "telegram", False, NOW)
    assert await validate_for_checkout(session, order.id, 42, order.amount, "RUB", 24, NOW)
    assert not await validate_for_checkout(session, order.id, 43, order.amount, "RUB", 24, NOW)  # other user
    assert not await validate_for_checkout(session, order.id, 42, 1, "RUB", 24, NOW)  # tampered amount
    assert not await validate_for_checkout(session, order.id, 42, order.amount, "RUB", 24, NOW + timedelta(days=2))


async def test_discount_promo_applied_to_order_and_consumed_on_payment(session, vpn):
    user = await make_user(session)
    await make_plan(session, price=10000)
    session.add(PromoCode(code="SALE20", kind=PromoKind.PERCENT, value=20))
    await session.commit()
    assert (await activate_promo(session, user, "sale20", NOW, vpn_ready=True)).ok
    order = await create_order(session, user, "m1", "test", True, NOW)
    assert order.discount == 2000 and order.amount == 8000
    await process_payment_event(session, ev(order), NOW)
    order2 = await create_order(session, user, "m1", "test", True, NOW)
    assert order2.discount == 0


def test_webhook_signature_verification():
    body, headers = provider.build_event("order-1", True, 100, "RUB")
    event = provider.parse_webhook(headers, body)
    assert event.is_test and event.success and event.amount == 100
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook({provider.SIGNATURE_HEADER: "0" * 64}, body)
    tampered = json.loads(body)
    tampered["amount"] = 1
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook(headers, json.dumps(tampered).encode())
    with pytest.raises(InvalidWebhookError):
        provider.parse_webhook({}, body)

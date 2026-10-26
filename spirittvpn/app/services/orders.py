"""Plans and orders. Price and duration always come from the DB, never from the client."""
from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ActivationStatus,
    Order,
    OrderStatus,
    PaymentEvent,
    Plan,
    PromoActivation,
    PromoCode,
    PromoKind,
    Subscription,
    User,
)
from app.models import BonusSource
from app.payments.providers import PaymentEventData
from app.services.formatting import min_amount
from app.services.referrals import Reward, grant_referral_bonus
from app.services.users import extend_subscription

log = logging.getLogger("spirittvpn.orders")

async def seed_plans(session: AsyncSession, catalog: list[dict], currency: str) -> None:
    """Creates catalog plans that don't exist yet. Existing plans (possibly edited by admins) are never overwritten."""
    existing = set((await session.scalars(select(Plan.code))).all())
    for item in catalog:
        if item["code"] not in existing:
            session.add(Plan(currency=currency, is_active=True, **item))
    await session.commit()


async def active_plans(session: AsyncSession) -> list[Plan]:
    return list(
        (await session.scalars(select(Plan).where(Plan.is_active, Plan.price > 0).order_by(Plan.sort_order))).all()
    )


async def get_active_plan(session: AsyncSession, code: str) -> Plan | None:
    return await session.scalar(select(Plan).where(Plan.code == code, Plan.is_active, Plan.price > 0))


async def pending_discount(session: AsyncSession, user: User) -> tuple[PromoActivation, PromoCode] | None:
    row = (
        await session.execute(
            select(PromoActivation, PromoCode)
            .join(PromoCode, PromoCode.id == PromoActivation.promo_id)
            .where(PromoActivation.user_id == user.id, PromoActivation.status == ActivationStatus.PENDING)
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row else None


def promo_applies(promo: PromoCode, plan_code: str) -> bool:
    if not promo.allowed_plans:
        return True
    return plan_code in {c.strip() for c in promo.allowed_plans.split(",")}


def apply_discount(price: int, currency: str, promo: PromoCode | None, plan_code: str | None = None) -> int:
    if promo is None or (plan_code and not promo_applies(promo, plan_code)):
        return 0
    if promo.kind == PromoKind.PERCENT:
        discount = price * promo.value // 100
    elif promo.kind == PromoKind.FIXED:
        discount = promo.value
    else:
        return 0
    return max(0, min(discount, price - min_amount(currency)))


@dataclass(frozen=True)
class Quote:
    plan: Plan
    original: int
    discount: int
    total: int
    promo_activation_id: int | None
    promo_code: str | None


async def quote(session: AsyncSession, user: User, plan: Plan) -> Quote:
    pd = await pending_discount(session, user)
    promo = pd[1] if pd else None
    discount = apply_discount(plan.price, plan.currency, promo, plan.code)
    return Quote(
        plan, plan.price, discount, plan.price - discount, pd[0].id if pd and discount else None, promo.code if promo else None
    )


async def create_order(session: AsyncSession, user: User, plan_code: str, provider: str, is_test: bool, now: datetime) -> Order | None:
    plan = await get_active_plan(session, plan_code)
    if plan is None:
        return None
    q = await quote(session, user, plan)
    order = Order(
        id=str(uuid.uuid4()),
        user_id=user.id,
        plan_id=plan.id,
        plan_code=plan.code,
        plan_title=plan.title,
        duration_days=plan.duration_days,
        device_limit=plan.device_limit,
        is_lifetime=plan.is_lifetime,
        amount_original=q.original,
        discount=q.discount,
        amount=q.total,
        currency=plan.currency,
        promo_activation_id=q.promo_activation_id,
        status=OrderStatus.CREATED,
        provider=provider,
        is_test=is_test,
        created_at=now,
    )
    session.add(order)
    await session.commit()
    log.info("order created id=%s user_id=%s plan=%s test=%s", order.id, user.id, plan.code, is_test)
    return order


async def mark_pending(session: AsyncSession, order: Order) -> None:
    await session.execute(
        update(Order).where(Order.id == order.id, Order.status == OrderStatus.CREATED).values(status=OrderStatus.PENDING)
    )
    await session.commit()
    await session.refresh(order)


async def mark_failed(session: AsyncSession, order_id: str, reason: str) -> None:
    await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status.in_(OrderStatus.PAYABLE))
        .values(status=OrderStatus.FAILED, failure_reason=reason[:255])
    )
    await session.commit()


async def validate_for_checkout(session: AsyncSession, order_id: str, telegram_id: int, amount: int, currency: str, ttl_hours: int, now: datetime) -> bool:
    """Telegram pre_checkout_query: last chance to refuse before money is taken."""
    row = (
        await session.execute(select(Order, User).join(User, User.id == Order.user_id).where(Order.id == order_id))
    ).first()
    if row is None:
        return False
    order, user = row
    return (
        user.telegram_id == telegram_id
        and not user.is_blocked
        and order.status in OrderStatus.PAYABLE
        and order.amount == amount
        and order.currency == currency
        and order.created_at + timedelta(hours=ttl_hours) > now
    )


class PaymentOutcome(enum.Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    MISMATCH = "mismatch"
    UNKNOWN_ORDER = "unknown_order"


@dataclass
class PaymentResult:
    outcome: PaymentOutcome
    order: Order | None = None
    user: User | None = None
    subscription: Subscription | None = None
    referral: Reward | None = None


async def process_payment_event(session: AsyncSession, ev: PaymentEventData, now: datetime) -> PaymentResult:
    """Idempotent: the same event (or a second event for a paid order) never extends twice."""
    event = PaymentEvent(
        provider=ev.provider,
        event_id=ev.event_id,
        order_id=ev.order_id,
        success=ev.success,
        amount=ev.amount,
        currency=ev.currency,
        is_test=ev.is_test,
        received_at=now,
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        log.info("duplicate payment event provider=%s order=%s", ev.provider, ev.order_id)
        return PaymentResult(PaymentOutcome.DUPLICATE, await session.get(Order, ev.order_id))

    order = await session.scalar(select(Order).where(Order.id == ev.order_id).with_for_update())
    if order is None:
        event.result = "unknown_order"
        await session.commit()
        log.warning("payment for unknown order provider=%s", ev.provider)
        return PaymentResult(PaymentOutcome.UNKNOWN_ORDER)
    user = await session.get(User, order.user_id)

    if order.status == OrderStatus.PAID:
        event.result = "already_paid"
        await session.commit()
        return PaymentResult(PaymentOutcome.DUPLICATE, order, user)

    if ev.provider != order.provider or ev.is_test != order.is_test:
        event.result = "provider_mismatch"
        await session.commit()
        log.error("provider/test-mode mismatch for order=%s", order.id)
        return PaymentResult(PaymentOutcome.MISMATCH, order, user)

    if not ev.success:
        event.result = "failed"
        if order.status in OrderStatus.PAYABLE:
            order.status = OrderStatus.FAILED
            order.failure_reason = "declined by provider"
        await session.commit()
        return PaymentResult(PaymentOutcome.FAILED, order, user)

    if ev.amount != order.amount or ev.currency != order.currency:
        event.result = "amount_mismatch"
        await session.commit()
        log.error("amount mismatch for order=%s", order.id)
        return PaymentResult(PaymentOutcome.MISMATCH, order, user)

    # Confirmed money is honoured even if the order was cancelled/failed meanwhile.
    res = await session.execute(
        update(Order)
        .where(Order.id == order.id, Order.status != OrderStatus.PAID)
        .values(status=OrderStatus.PAID, paid_at=now, provider_payment_id=ev.event_id, updated_at=now)
    )
    if res.rowcount != 1:
        await session.rollback()
        return PaymentResult(PaymentOutcome.DUPLICATE, await session.get(Order, ev.order_id))

    assert user is not None
    first_payment = not await session.scalar(
        select(Order.id).where(Order.user_id == user.id, Order.status == OrderStatus.PAID, Order.id != order.id).limit(1)
    )
    sub = await extend_subscription(
        session, user, order.duration_days, now, plan_code=order.plan_code, plan_title=order.plan_title,
        lifetime=order.is_lifetime, device_limit=order.device_limit,
    )
    reward = await grant_referral_bonus(session, user, BonusSource.REFERRAL_PAYMENT, now) if first_payment else None
    if order.promo_activation_id:
        await session.execute(
            update(PromoActivation)
            .where(PromoActivation.id == order.promo_activation_id, PromoActivation.status == ActivationStatus.PENDING)
            .values(status=ActivationStatus.USED, used_at=now)
        )
    event.result = "applied"
    await session.commit()
    await session.refresh(order)
    log.info("payment applied order=%s user_id=%s days=%s test=%s", order.id, user.id, order.duration_days, order.is_test)
    return PaymentResult(PaymentOutcome.APPLIED, order, user, sub, reward)


async def refund_order(session: AsyncSession, order_id: str, now: datetime) -> Order | None:
    """Marks a paid order refunded and takes back the purchased period. Caller audits and commits."""
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None or order.status != OrderStatus.PAID:
        return None
    order.status = OrderStatus.REFUNDED
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == order.user_id).with_for_update())
    if sub is not None:
        if order.is_lifetime:
            sub.is_lifetime = False
            sub.expires_at = now
        else:
            sub.expires_at = max(now, sub.expires_at - timedelta(days=order.duration_days))
        sub.vpn_sync_needed = True
    await session.flush()
    return order


async def cancel_stale_orders(session: AsyncSession, ttl_hours: int, now: datetime) -> int:
    res = await session.execute(
        update(Order)
        .where(Order.status.in_(OrderStatus.PAYABLE), Order.created_at < now - timedelta(hours=ttl_hours))
        .values(status=OrderStatus.CANCELLED, failure_reason="expired")
    )
    await session.commit()
    return res.rowcount or 0


async def user_orders(session: AsyncSession, user_id: int, limit: int = 10) -> list[Order]:
    return list(
        (await session.scalars(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(limit))).all()
    )

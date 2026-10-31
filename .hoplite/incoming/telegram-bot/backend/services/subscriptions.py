"""Subscription lifecycle (database side). VPN synchronisation lives in services/devices.py."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Device, DevStatus, Order, Subscription, SubscriptionEvent, SubStatus
from backend.services.context import ServiceError
from backend.utils import utcnow


async def get_current(session: AsyncSession, user_id: int, *, lock: bool = False) -> Subscription | None:
    stmt = select(Subscription).where(Subscription.user_id == user_id, Subscription.status.in_(SubStatus.CURRENT))
    if lock:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


def is_usable(sub: Subscription | None, now: datetime | None = None) -> bool:
    if sub is None or sub.status != SubStatus.ACTIVE:
        return False
    if sub.is_lifetime:
        return True
    return sub.expires_at is not None and sub.expires_at > (now or utcnow())


def log_event(
    session: AsyncSession,
    sub: Subscription,
    action: str,
    *,
    actor: str,
    old_status: str | None = None,
    old_expires: datetime | None = None,
    order_id: int | None = None,
    days: int | None = None,
    comment: str | None = None,
) -> SubscriptionEvent:
    ev = SubscriptionEvent(
        subscription_id=sub.id,
        user_id=sub.user_id,
        action=action,
        order_id=order_id,
        old_status=old_status,
        new_status=sub.status,
        old_expires_at=old_expires,
        new_expires_at=sub.expires_at,
        days=days,
        actor=actor,
        comment=comment,
    )
    session.add(ev)
    return ev


async def apply_paid_order(session: AsyncSession, order: Order) -> Subscription:
    """Apply a confirmed order. Must run in the transaction that marks the order paid.

    The unique index on subscription_events.order_id makes a second application fail loudly.
    """
    now = utcnow()
    sub = await get_current(session, order.user_id, lock=True)
    if sub is None:
        sub = Subscription(
            user_id=order.user_id,
            plan_id=order.plan_id,
            plan_code=order.plan_code,
            status=SubStatus.ACTIVE,
            is_lifetime=order.is_lifetime,
            device_limit=order.device_limit,
            source="payment",
            order_id=order.id,
            starts_at=now,
            expires_at=None if order.is_lifetime else now + timedelta(days=order.duration_days or 0),
        )
        session.add(sub)
        await session.flush()
        log_event(session, sub, "created", actor="system", order_id=order.id, days=order.duration_days)
        return sub

    old_status, old_expires = sub.status, sub.expires_at
    sub.plan_id, sub.plan_code, sub.order_id, sub.source = order.plan_id, order.plan_code, order.id, "payment"
    sub.device_limit = order.device_limit
    if order.is_lifetime:
        sub.is_lifetime = True
        sub.expires_at = None
        if sub.status != SubStatus.SUSPENDED:
            sub.status = SubStatus.ACTIVE
        sub.starts_at = sub.starts_at or now
        action = "lifetime"
    elif sub.is_lifetime:
        # Guarded at order creation; if it happens anyway the lifetime stays and only the limit changes.
        action = "noop_lifetime"
    else:
        days = order.duration_days or 0
        if sub.status == SubStatus.ACTIVE and sub.expires_at and sub.expires_at > now:
            sub.expires_at = sub.expires_at + timedelta(days=days)
            action = "extended"
        elif sub.status == SubStatus.SUSPENDED:
            base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
            sub.expires_at = base + timedelta(days=days)
            action = "extended_suspended"
        else:
            sub.status = SubStatus.ACTIVE
            sub.starts_at = now
            sub.expires_at = now + timedelta(days=days)
            action = "reactivated"
    await session.flush()
    log_event(
        session, sub, action, actor="system", old_status=old_status, old_expires=old_expires, order_id=order.id,
        days=order.duration_days,
    )
    return sub


async def add_days(session: AsyncSession, sub: Subscription, days: int, *, action: str, actor: str, comment: str | None = None) -> None:
    if sub.is_lifetime:
        raise ServiceError("У бессрочной подписки нет срока окончания — продлевать не нужно.", "lifetime")
    if sub.status not in (SubStatus.ACTIVE, SubStatus.EXPIRED, SubStatus.SUSPENDED):
        raise ServiceError("Подписка не активна.", "not_active")
    now = utcnow()
    old_status, old_expires = sub.status, sub.expires_at
    base = sub.expires_at if sub.expires_at and sub.expires_at > now else now
    sub.expires_at = base + timedelta(days=days)
    if sub.status == SubStatus.EXPIRED:
        sub.status = SubStatus.ACTIVE
        sub.starts_at = now
    log_event(session, sub, action, actor=actor, old_status=old_status, old_expires=old_expires, days=days, comment=comment)


async def set_expiry(session: AsyncSession, sub: Subscription, new_expires: datetime, *, actor: str, comment: str) -> None:
    if sub.is_lifetime:
        raise ServiceError("Для Lifetime срок окончания не задаётся.", "lifetime")
    now = utcnow()
    old_status, old_expires = sub.status, sub.expires_at
    sub.expires_at = new_expires
    if sub.status == SubStatus.EXPIRED and new_expires > now:
        sub.status = SubStatus.ACTIVE
    elif sub.status == SubStatus.ACTIVE and new_expires <= now:
        sub.status = SubStatus.EXPIRED
    log_event(session, sub, "admin_set_expiry", actor=actor, old_status=old_status, old_expires=old_expires, comment=comment)


async def expire_due(session: AsyncSession) -> list[int]:
    """Mark limited subscriptions past their end date as expired. Lifetime is never touched."""
    now = utcnow()
    subs = (
        await session.execute(
            select(Subscription)
            .where(
                Subscription.status == SubStatus.ACTIVE,
                Subscription.is_lifetime.is_(False),
                Subscription.expires_at <= now,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for sub in subs:
        sub.status = SubStatus.EXPIRED
        log_event(session, sub, "expired", actor="system", old_status=SubStatus.ACTIVE, old_expires=sub.expires_at)
        await session.execute(
            update(Device)
            .where(Device.subscription_id == sub.id, Device.status == DevStatus.ACTIVE)
            .values(status=DevStatus.EXPIRED)
        )
    return [s.id for s in subs]

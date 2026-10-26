"""Users, legal acceptance and subscription arithmetic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LegalAcceptance, Order, OrderStatus, Subscription, SubStatus, TrialActivation, User


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def register_user(
    session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None, now: datetime
) -> tuple[User, bool]:
    """Returns (user, created). Never resets existing data on repeated /start."""
    user = await get_user(session, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name, created_at=now, last_seen_at=now)
        session.add(user)
        try:
            await session.commit()
            return user, True
        except IntegrityError:  # concurrent first update from the same user
            await session.rollback()
            user = await get_user(session, telegram_id)
            assert user is not None
    user.username, user.first_name, user.last_seen_at = username, first_name, now
    user.bot_blocked = False
    await session.commit()
    return user, False


async def has_accepted_terms(session: AsyncSession, user: User, version: str) -> bool:
    return (
        await session.scalar(
            select(LegalAcceptance.id).where(LegalAcceptance.user_id == user.id, LegalAcceptance.version == version)
        )
    ) is not None


async def get_acceptance(session: AsyncSession, user: User, version: str) -> LegalAcceptance | None:
    return await session.scalar(
        select(LegalAcceptance).where(LegalAcceptance.user_id == user.id, LegalAcceptance.version == version)
    )


async def accept_terms(session: AsyncSession, user: User, version: str, now: datetime) -> None:
    """Called only from an explicit «Принимаю» button press."""
    if await has_accepted_terms(session, user, version):
        return
    session.add(LegalAcceptance(user_id=user.id, version=version, accepted_at=now))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()


async def get_subscription(session: AsyncSession, user_id: int, *, for_update: bool = False) -> Subscription | None:
    stmt = select(Subscription).where(Subscription.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def trial_used(session: AsyncSession, user_id: int) -> bool:
    return (await session.scalar(select(TrialActivation.id).where(TrialActivation.user_id == user_id))) is not None


async def has_paid_orders(session: AsyncSession, user_id: int) -> bool:
    return (
        await session.scalar(select(Order.id).where(Order.user_id == user_id, Order.status == OrderStatus.PAID).limit(1))
    ) is not None


def is_active(sub: Subscription | None, now: datetime) -> bool:
    return sub is not None and sub.status == SubStatus.ACTIVE and sub.expires_at > now


def compute_period(current: Subscription | None, now: datetime, days: int) -> tuple[datetime, datetime]:
    """Sequential renewal: an active subscription is extended from its end, an expired one from now."""
    if days <= 0:
        raise ValueError("days must be positive")
    if is_active(current, now):
        assert current is not None
        return current.starts_at, current.expires_at + timedelta(days=days)
    return now, now + timedelta(days=days)


async def extend_subscription(
    session: AsyncSession,
    user: User,
    days: int,
    now: datetime,
    *,
    plan_code: str | None = None,
    plan_title: str | None = None,
    is_trial: bool = False,
) -> Subscription:
    """Adds days inside the caller's transaction (caller commits). Marks VPN for synchronisation."""
    sub = await get_subscription(session, user.id, for_update=True)
    starts_at, expires_at = compute_period(sub, now, days)
    if sub is None:
        sub = Subscription(user_id=user.id, starts_at=starts_at, expires_at=expires_at)
        session.add(sub)
    else:
        sub.starts_at, sub.expires_at = starts_at, expires_at
    sub.status = SubStatus.ACTIVE
    if plan_code is not None:
        sub.plan_code, sub.plan_title, sub.is_trial = plan_code, plan_title, False
    elif is_trial:
        sub.plan_code, sub.plan_title, sub.is_trial = "trial", "Пробный период", True
    elif sub.plan_code is None:
        sub.plan_code, sub.plan_title, sub.is_trial = "bonus", "Бонусные дни", False
    sub.vpn_sync_needed = True
    sub.vpn_sync_attempts = 0
    await session.flush()
    return sub


@dataclass(frozen=True)
class AccountView:
    status: str  # active | expired | none
    subscription: Subscription | None
    trial_used: bool
    remaining: timedelta | None


async def account_view(session: AsyncSession, user: User, now: datetime) -> AccountView:
    sub = await get_subscription(session, user.id)
    used = await trial_used(session, user.id)
    if sub is None:
        return AccountView("none", None, used, None)
    if is_active(sub, now):
        return AccountView("active", sub, used, sub.expires_at - now)
    return AccountView("expired", sub, used, None)

"""Referral program: one referrer per user (set at registration), one-shot bonuses via BonusEntry.unique_key."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BonusEntry, BonusSource, Order, OrderStatus, TrialActivation, User
from app.services import settings_store
from app.services.users import extend_subscription, get_subscription, is_active

REF_PREFIX = "ref_"


def parse_ref_payload(payload: str | None) -> int | None:
    if not payload or not payload.startswith(REF_PREFIX):
        return None
    raw = payload[len(REF_PREFIX):]
    return int(raw) if raw.isdigit() and len(raw) <= 15 else None


def referral_link(bot_username: str | None, telegram_id: int) -> str | None:
    return f"https://t.me/{bot_username}?start={REF_PREFIX}{telegram_id}" if bot_username else None


def share_url(link: str) -> str:
    text = "Подключайся к SpirittVPN — быстрый и надёжный VPN прямо в Telegram. Забирай пробный период по моей ссылке!"
    return f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(text, safe='')}"


def mask_name(user: User) -> str:
    name = (user.first_name or "").strip()
    return f"{name[0]}•••" if name else "Друг"


@dataclass(frozen=True)
class Reward:
    referrer_user_id: int
    referrer_telegram_id: int
    days: int
    kind: str


KIND_SETTING = {BonusSource.REFERRAL_TRIAL: "referral_trial_days", BonusSource.REFERRAL_PAYMENT: "referral_payment_days"}
KIND_TEXT = {BonusSource.REFERRAL_TRIAL: "Друг активировал пробный период",
             BonusSource.REFERRAL_PAYMENT: "Друг оплатил подписку"}


async def grant_referral_bonus(session: AsyncSession, referred: User, kind: str, now: datetime) -> Reward | None:
    """Runs inside the caller's transaction. UNIQUE(unique_key) guarantees one bonus per invitee and kind."""
    if referred.referrer_id is None or not await settings_store.get_bool(session, "referral_enabled"):
        return None
    days = await settings_store.get_int(session, KIND_SETTING[kind])
    if days <= 0:
        return None
    key = f"{kind}:{referred.id}"
    if await session.scalar(select(BonusEntry.id).where(BonusEntry.unique_key == key)):
        return None
    referrer = await session.get(User, referred.referrer_id)
    if referrer is None or referrer.is_blocked:
        return None
    session.add(BonusEntry(user_id=referrer.id, source=kind, days=days, unique_key=key, related_user_id=referred.id,
                           description=KIND_TEXT[kind], created_at=now))
    await session.flush()
    sub = await get_subscription(session, referrer.id)
    if not (sub and sub.is_lifetime and is_active(sub, now)):
        await extend_subscription(session, referrer, days, now)
    return Reward(referrer.id, referrer.telegram_id, days, kind)


@dataclass(frozen=True)
class ReferralSummary:
    invited: int
    bonus_days: int
    pending: int


async def summary(session: AsyncSession, user: User) -> ReferralSummary:
    invited = await session.scalar(select(func.count(User.id)).where(User.referrer_id == user.id)) or 0
    days = await session.scalar(
        select(func.coalesce(func.sum(BonusEntry.days), 0)).where(
            BonusEntry.user_id == user.id, BonusEntry.source.in_(BonusSource.REFERRAL)
        )
    ) or 0
    paid_bonus = await session.scalar(
        select(func.count(BonusEntry.id)).where(BonusEntry.user_id == user.id,
                                                BonusEntry.source == BonusSource.REFERRAL_PAYMENT)
    ) or 0
    return ReferralSummary(invited, int(days), max(invited - paid_bonus, 0))


@dataclass(frozen=True)
class Invite:
    name: str
    invited_at: datetime
    trial_done: bool
    payment_done: bool
    bonus_days: int


async def invites(session: AsyncSession, user: User, limit: int = 50) -> list[Invite]:
    """No Telegram IDs, usernames or payment data of invitees are exposed."""
    friends = (await session.scalars(
        select(User).where(User.referrer_id == user.id).order_by(User.created_at.desc()).limit(limit)
    )).all()
    out = []
    for f in friends:
        trial = await session.scalar(select(TrialActivation.id).where(TrialActivation.user_id == f.id))
        paid = await session.scalar(select(Order.id).where(Order.user_id == f.id, Order.status == OrderStatus.PAID).limit(1))
        days = await session.scalar(
            select(func.coalesce(func.sum(BonusEntry.days), 0)).where(
                BonusEntry.user_id == user.id, BonusEntry.related_user_id == f.id)
        ) or 0
        out.append(Invite(mask_name(f), f.created_at, trial is not None, paid is not None, int(days)))
    return out


async def bonus_history(session: AsyncSession, user_id: int, limit: int = 50) -> list[BonusEntry]:
    return list((await session.scalars(
        select(BonusEntry).where(BonusEntry.user_id == user_id).order_by(BonusEntry.created_at.desc(), BonusEntry.id.desc())
        .limit(limit)
    )).all())


async def program_stats(session: AsyncSession) -> dict:
    referred = select(User.id).where(User.referrer_id.is_not(None))
    return {
        "invites": await session.scalar(select(func.count()).select_from(referred.subquery())) or 0,
        "trials": await session.scalar(
            select(func.count(TrialActivation.id)).where(TrialActivation.user_id.in_(referred))) or 0,
        "paid": await session.scalar(
            select(func.count(func.distinct(Order.user_id))).where(Order.status == OrderStatus.PAID,
                                                                   Order.user_id.in_(referred))) or 0,
        "days_granted": int(await session.scalar(
            select(func.coalesce(func.sum(BonusEntry.days), 0)).where(
                BonusEntry.source.in_((*BonusSource.REFERRAL, BonusSource.CORRECTION)))) or 0),
        "enabled": await settings_store.get_bool(session, "referral_enabled"),
        "trial_days": await settings_store.get_int(session, "referral_trial_days"),
        "payment_days": await settings_store.get_int(session, "referral_payment_days"),
    }


async def apply_correction(session: AsyncSession, user: User, days: int, reason: str, admin_id: int, now: datetime) -> BonusEntry:
    """Manual bonus correction (positive or negative days). Caller audits and commits."""
    if days > 0:
        await extend_subscription(session, user, days, now)
    else:
        sub = await get_subscription(session, user.id, for_update=True)
        if sub is None or sub.is_lifetime:
            raise ValueError("Нечего уменьшать: нет срочной подписки")
        sub.expires_at = max(now, sub.expires_at + timedelta(days=days))
        sub.vpn_sync_needed = True
    entry = BonusEntry(user_id=user.id, source=BonusSource.CORRECTION, days=days, description=reason[:255],
                       created_by=admin_id, created_at=now)
    session.add(entry)
    await session.flush()
    return entry

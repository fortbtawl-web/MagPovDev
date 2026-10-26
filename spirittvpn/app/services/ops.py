"""Support tickets, broadcasts and background subscription lifecycle."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Broadcast, Subscription, SubStatus, SupportMessage, SupportTicket, User, VPNAccount
from app.services import settings_store
from app.services.users import is_active
from app.services.vpn_sync import sync_user_vpn
from app.vpn.base import VPNError, VPNProvider

log = logging.getLogger("spirittvpn.jobs")

# (telegram_id, text, keyboard) -> delivered?  False means the user blocked the bot.
Notifier = Callable[[int, str, InlineKeyboardMarkup | None], Awaitable[bool]]


# ---------- support ----------

async def create_ticket(session: AsyncSession, user: User, topic: str, text: str) -> SupportTicket:
    ticket = SupportTicket(user_id=user.id, topic=topic[:32])
    session.add(ticket)
    await session.flush()
    session.add(SupportMessage(ticket_id=ticket.id, text=text[:2000]))
    await session.commit()
    return ticket


async def add_admin_reply(session: AsyncSession, ticket_id: int, text: str) -> tuple[SupportTicket, User] | None:
    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None:
        return None
    user = await session.get(User, ticket.user_id)
    session.add(SupportMessage(ticket_id=ticket.id, from_admin=True, text=text[:3500]))
    ticket.status = "answered"
    await session.commit()
    assert user is not None
    return ticket, user


async def close_ticket(session: AsyncSession, ticket_id: int) -> bool:
    res = await session.execute(update(SupportTicket).where(SupportTicket.id == ticket_id).values(status="closed"))
    await session.commit()
    return bool(res.rowcount)


async def open_tickets(session: AsyncSession) -> list[tuple[SupportTicket, User]]:
    rows = await session.execute(
        select(SupportTicket, User).join(User, User.id == SupportTicket.user_id)
        .where(SupportTicket.status != "closed").order_by(SupportTicket.id.desc()).limit(20)
    )
    return [(t, u) for t, u in rows.all()]


# ---------- broadcasts ----------

async def run_broadcast(
    session_factory: async_sessionmaker[AsyncSession], broadcast_id: int, notify: Notifier, rate_per_second: float
) -> Broadcast:
    async with session_factory() as session:
        bc = await session.get(Broadcast, broadcast_id)
        assert bc is not None
        res = await session.execute(
            update(Broadcast).where(Broadcast.id == broadcast_id, Broadcast.status == "draft").values(status="sending")
        )
        await session.commit()
        if res.rowcount != 1:  # already sent / cancelled — protects against double confirmation
            return bc
        last_id = 0
        delay = 1.0 / rate_per_second
        while True:
            users = (
                await session.scalars(
                    select(User).where(User.id > last_id, User.bot_blocked.is_(False), User.is_blocked.is_(False))
                    .order_by(User.id).limit(500)
                )
            ).all()
            if not users:
                break
            for user in users:
                last_id = user.id
                try:
                    delivered = await notify(user.telegram_id, bc.text, None)
                except Exception:  # noqa: BLE001 - one bad recipient must not stop the broadcast
                    log.exception("broadcast delivery error")
                    bc.failed += 1
                else:
                    if delivered:
                        bc.sent += 1
                    else:
                        bc.blocked += 1
                        user.bot_blocked = True
                await asyncio.sleep(delay)
            await session.commit()
        bc.status = "done"
        bc.finished_at = datetime.now().astimezone()
        await session.commit()
        return bc


# ---------- lifecycle ----------

async def _claim(session: AsyncSession, sub: Subscription, column, extra: dict | None = None) -> bool:
    """At-most-once marker: only one worker/run wins the UPDATE for a given expiry value."""
    res = await session.execute(
        update(Subscription)
        .where(Subscription.id == sub.id, Subscription.expires_at == sub.expires_at,
               or_(column.is_(None), column != sub.expires_at))
        .values({column.key: sub.expires_at, **(extra or {})})
    )
    await session.commit()
    return res.rowcount == 1


async def send_expiry_notices(session: AsyncSession, notify: Notifier, now: datetime, final_hours: int, fmt, kb) -> int:
    if not await settings_store.get_bool(session, "notifications_enabled"):
        return 0
    sent = 0
    from app import texts

    rows = (
        await session.execute(
            select(Subscription, User).join(User, User.id == Subscription.user_id).where(
                Subscription.status == SubStatus.ACTIVE, Subscription.expires_at > now,
                Subscription.expires_at <= now + timedelta(hours=24),
            )
        )
    ).all()
    for sub, user in rows:
        left = sub.expires_at - now
        if left <= timedelta(hours=final_hours):
            column, text = Subscription.notified_final_for, texts.notice_final(fmt(sub.expires_at))
            if sub.notified_final_for == sub.expires_at:
                continue
        else:
            column, text = Subscription.notified_24h_for, texts.notice_24h(fmt(sub.expires_at))
            if sub.notified_24h_for == sub.expires_at:
                continue
        if await _claim(session, sub, column):
            if not await notify(user.telegram_id, text, kb):
                user.bot_blocked = True
                await session.commit()
            sent += 1
    return sent


async def expire_subscriptions(
    session: AsyncSession, provider: VPNProvider, notify: Notifier, now: datetime, kb, vpn_attempts: int = 3
) -> int:
    from app import texts

    notify_on = await settings_store.get_bool(session, "notifications_enabled")
    rows = (
        await session.execute(
            select(Subscription, User).join(User, User.id == Subscription.user_id)
            .where(Subscription.status == SubStatus.ACTIVE, Subscription.expires_at <= now)
        )
    ).all()
    count = 0
    for sub, user in rows:
        res = await session.execute(
            update(Subscription)
            .where(Subscription.id == sub.id, Subscription.status == SubStatus.ACTIVE, Subscription.expires_at <= now)
            .values(status=SubStatus.EXPIRED, vpn_sync_needed=True, vpn_sync_attempts=0, notified_expired_for=sub.expires_at)
        )
        await session.commit()
        if res.rowcount != 1:
            continue
        count += 1
        await session.refresh(sub)
        await sync_user_vpn(session, provider, user, sub, now, vpn_attempts)
        if notify_on and not await notify(user.telegram_id, texts.notice_expired(sub.is_trial), kb):
            user.bot_blocked = True
            await session.commit()
    return count


async def retry_vpn_sync(
    session: AsyncSession, provider: VPNProvider, now: datetime, max_attempts: int, vpn_attempts: int = 3
) -> tuple[int, int]:
    if not provider.is_configured:
        return 0, 0
    rows = (
        await session.execute(
            select(Subscription, User).join(User, User.id == Subscription.user_id)
            .where(Subscription.vpn_sync_needed, Subscription.vpn_sync_attempts < max_attempts).limit(200)
        )
    ).all()
    ok = failed = 0
    for sub, user in rows:
        if await sync_user_vpn(session, provider, user, sub, now, vpn_attempts):
            ok += 1
        else:
            failed += 1
    return ok, failed


async def reconcile_vpn(session: AsyncSession, provider: VPNProvider, now: datetime) -> int:
    """Flags users whose VPN-side state differs from the DB; retry_vpn_sync then fixes them."""
    if not provider.is_configured:
        return 0
    rows = (
        await session.execute(
            select(Subscription, User).join(User, User.id == Subscription.user_id)
            .join(VPNAccount, VPNAccount.user_id == User.id).where(Subscription.vpn_sync_needed.is_(False))
        )
    ).all()
    mismatches = 0
    for sub, user in rows:
        try:
            state = await provider.get_state(user.telegram_id)
        except VPNError as exc:
            log.warning("reconcile aborted: %s", type(exc).__name__)
            break
        want_active = is_active(sub, now)
        drift = (
            (state is None and want_active)
            or (state is not None and state.enabled != want_active)
            or (state is not None and want_active and state.expires_at is not None
                and abs((state.expires_at - sub.expires_at).total_seconds()) > 120)
        )
        if drift:
            sub.vpn_sync_needed = True
            mismatches += 1
    await session.commit()
    if mismatches:
        log.warning("reconcile: %d mismatches flagged for resync", mismatches)
    return mismatches

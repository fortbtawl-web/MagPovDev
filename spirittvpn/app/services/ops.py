"""Support tickets, broadcasts and background subscription lifecycle."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Broadcast,
    Device,
    DeviceStatus,
    Subscription,
    SubStatus,
    SupportMessage,
    SupportTicket,
    User,
    VPNErrorLog,
    VPNNode,
)
from app.services import settings_store
from app.services.devices import create_with_recovery, sync_user_vpn
from app.services.users import is_active
from app.vpn.base import VPNError, VPNNotConfiguredError, VPNProvider

log = logging.getLogger("spirittvpn.jobs")

# (telegram_id, text, keyboard) -> delivered?  False means the user blocked the bot.
Notifier = Callable[[int, str, InlineKeyboardMarkup | None], Awaitable[bool]]


# ---------- support ----------

TICKET_CATEGORIES = {"general": "Общий вопрос", "connection": "Подключение", "payment": "Оплата",
                     "subscription": "Подписка", "other": "Другое"}


async def create_ticket(session: AsyncSession, user: User, topic: str, text: str) -> SupportTicket:
    ticket = SupportTicket(user_id=user.id, topic=topic if topic in TICKET_CATEGORIES else "general")
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


async def user_tickets(session: AsyncSession, user_id: int) -> list[tuple[SupportTicket, list[SupportMessage]]]:
    """Only the caller's own tickets."""
    tickets = (await session.scalars(
        select(SupportTicket).where(SupportTicket.user_id == user_id).order_by(SupportTicket.id.desc()).limit(20)
    )).all()
    out = []
    for t in tickets:
        msgs = (await session.scalars(
            select(SupportMessage).where(SupportMessage.ticket_id == t.id).order_by(SupportMessage.id))).all()
        out.append((t, list(msgs)))
    return out


async def ticket_messages(session: AsyncSession, ticket_id: int) -> list[SupportMessage]:
    return list((await session.scalars(
        select(SupportMessage).where(SupportMessage.ticket_id == ticket_id).order_by(SupportMessage.id))).all())


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
                Subscription.status == SubStatus.ACTIVE, Subscription.is_lifetime.is_(False),
                Subscription.expires_at > now, Subscription.expires_at <= now + timedelta(hours=24),
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
            if not user.notifications_enabled:
                continue
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
            .where(Subscription.status == SubStatus.ACTIVE, Subscription.is_lifetime.is_(False),
                   Subscription.expires_at <= now)
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
        if notify_on and user.notifications_enabled and not await notify(user.telegram_id, texts.notice_expired(sub.is_trial), kb):
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
    """Compares every live device with the panel. Drift is flagged for resync; clients deleted behind our back
    are recreated (the user gets a new link)."""
    if not provider.is_configured:
        return 0
    rows = (
        await session.execute(
            select(Device, Subscription, User).join(User, User.id == Device.user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Device.status.in_(DeviceStatus.LIVE), Device.external_id.is_not(None), Subscription.vpn_sync_needed.is_(False))
        )
    ).all()
    mismatches = 0
    for device, sub, user in rows:
        try:
            state = await provider.get_client(device.external_id)
            want_active = is_active(sub, now) and device.status == DeviceStatus.ACTIVE
            if state is None:
                mismatches += 1
                if want_active:
                    client = await create_with_recovery(provider, device.external_username, user.telegram_id, sub.expires_at)
                    device.external_id = client.external_id
                    device.last_error = "recreated_on_panel"
                else:
                    device.status = DeviceStatus.REVOKED
                    device.revoked_at = now
                continue
        except VPNError as exc:
            session.add(VPNErrorLog(operation="reconcile", error=type(exc).__name__))
            log.warning("reconcile aborted: %s", type(exc).__name__)
            break
        drift = state.enabled != want_active or (
            want_active and state.expires_at is not None and abs((state.expires_at - sub.expires_at).total_seconds()) > 120
        )
        if drift:
            sub.vpn_sync_needed = True
            mismatches += 1
    await session.commit()
    if mismatches:
        log.warning("reconcile: %d mismatches", mismatches)
    return mismatches


async def check_nodes(session: AsyncSession, provider: VPNProvider, now: datetime) -> tuple[int, int]:
    """Refreshes node status from the panel. A failed check never leaves nodes shown as online."""
    if not provider.is_configured:
        return 0, 0
    try:
        nodes = await provider.list_nodes()
    except (VPNError, VPNNotConfiguredError) as exc:
        session.add(VPNErrorLog(operation="list_nodes", error=f"{type(exc).__name__}: {str(exc)[:200]}"))
        for node in (await session.scalars(select(VPNNode))).all():
            node.is_connected = False
            node.status_message = "Проверка не удалась: панель недоступна"
            node.last_checked_at = now
        await session.commit()
        return 0, 0
    seen = set()
    for info in nodes:
        seen.add(info.external_id)
        node = await session.scalar(select(VPNNode).where(VPNNode.external_id == info.external_id))
        if node is None:
            node = VPNNode(external_id=info.external_id, name=info.name)
            session.add(node)
        node.name, node.address, node.country = info.name, info.address, info.country
        node.is_connected, node.is_disabled, node.users_online = info.is_connected, info.is_disabled, info.users_online
        node.status_message, node.is_test, node.last_checked_at = info.status_message, provider.is_test, now
        if info.is_connected and not info.is_disabled:
            node.last_seen_ok_at = now
    for node in (await session.scalars(select(VPNNode))).all():
        if node.external_id not in seen:
            await session.delete(node)
    await session.commit()
    return len(nodes), sum(1 for n in nodes if n.is_connected and not n.is_disabled)

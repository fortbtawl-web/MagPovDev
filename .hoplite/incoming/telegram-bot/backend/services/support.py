"""Support tickets. Every read/write by a user is scoped to their own tickets."""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from sqlalchemy import func, select

from backend.models import AppSetting, SupportMessage, SupportTicket, TicketStatus, User
from backend.services import roles
from backend.services import settings as settings_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.support")

CATEGORIES = {
    "payment": "Оплата",
    "connection": "Подключение",
    "subscription": "Подписка",
    "devices": "Устройства",
    "app_error": "Ошибка приложения",
    "other": "Другое",
}
STATUS_LABELS = {
    TicketStatus.NEW: "Новое",
    TicketStatus.IN_PROGRESS: "В работе",
    TicketStatus.WAITING_USER: "Ожидает ответа пользователя",
    TicketStatus.RESOLVED: "Решено",
    TicketStatus.CLOSED: "Закрыто",
}
MAX_TEXT = 4000


def _clean(text: str | None, *, min_len: int) -> str:
    text = (text or "").strip()
    if len(text) < min_len:
        raise ServiceError(f"Сообщение слишком короткое (минимум {min_len} символов).", "too_short")
    if len(text) > MAX_TEXT:
        raise ServiceError(f"Сообщение слишком длинное (максимум {MAX_TEXT} символов).", "too_long")
    return text


async def _new_number(session) -> str:
    for digits in (5, 5, 5, 6, 6, 7):
        number = f"SUP-{secrets.randbelow(9 * 10 ** (digits - 1)) + 10 ** (digits - 1)}"
        if not await session.scalar(select(SupportTicket.id).where(SupportTicket.number == number)):
            return number
    raise RuntimeError("could not allocate ticket number")


def _preview(text: str) -> str:
    line = " ".join(text.split())
    return line[:80] + ("…" if len(line) > 80 else "")


async def _admin_buttons(ctx: AppContext, ticket_id: int) -> list[tuple[str, str]]:
    return [("📂 Открыть обращение", f"sup:a:{ticket_id}")]


async def notify_admins(ctx: AppContext, text: str, ticket: SupportTicket) -> None:
    targets = [ticket.assigned_admin_id] if ticket.assigned_admin_id else sorted(await roles.admin_ids(ctx))
    for admin_id in targets:
        try:
            await ctx.notifier.send(admin_id, text, buttons=await _admin_buttons(ctx, ticket.id))
        except Exception:  # noqa: BLE001
            log.exception("support notify failed")


def _reset_key(user_id: int) -> str:
    return f"support_reset:{user_id}"


async def _limit_window_start(session, user_id: int):
    """Start of the counting window: 24 h back, or the moment an admin reset this user's limit."""
    start = utcnow() - timedelta(days=1)
    row = await session.get(AppSetting, _reset_key(user_id))
    if row is not None:
        from datetime import datetime

        try:
            reset_at = datetime.fromisoformat(str(row.value))
        except ValueError:
            return start
        if reset_at > start:
            return reset_at
    return start


async def daily_usage(ctx: AppContext, user_id: int) -> tuple[int, int]:
    """(tickets created in the current window, daily limit)."""
    async with ctx.session() as session:
        limit = int(await settings_svc.get(session, "support_daily_limit"))
        start = await _limit_window_start(session, user_id)
        used = await session.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user_id, SupportTicket.created_at > start))
    return int(used or 0), limit


async def create_ticket(
    ctx: AppContext, user_id: int, category: str, text: str, *, attachment: tuple[str, str] | None = None
) -> tuple[SupportTicket, SupportMessage]:
    if category not in CATEGORIES:
        raise ServiceError("Выберите категорию обращения.", "bad_category")
    text = _clean(text, min_len=3 if attachment else 10)
    async with ctx.session() as session, session.begin():
        if not await settings_svc.get(session, "support_enabled"):
            raise ServiceError("Поддержка временно недоступна.", "support_disabled")
        await session.execute(select(User.id).where(User.id == user_id).with_for_update())
        max_open = int(await settings_svc.get(session, "support_max_open_tickets"))
        open_count = await session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.user_id == user_id, SupportTicket.status.in_(TicketStatus.OPEN))
        )
        if int(open_count or 0) >= max_open:
            raise ServiceError(f"У вас уже {open_count} открытых обращений. Дождитесь ответа или закройте одно из них.", "too_many_open")
        daily_limit = int(await settings_svc.get(session, "support_daily_limit"))
        window_start = await _limit_window_start(session, user_id)
        recent = (
            await session.execute(
                select(SupportTicket.created_at).where(SupportTicket.user_id == user_id, SupportTicket.created_at > window_start).order_by(SupportTicket.created_at)
            )
        ).scalars().all()
        if len(recent) >= daily_limit:
            wait = recent[len(recent) - daily_limit] + timedelta(days=1) - utcnow()
            hours, minutes = divmod(max(1, int(wait.total_seconds() // 60)), 60)
            raise ServiceError(
                f"Лимит обращений: {daily_limit} в сутки. Новое обращение можно создать через {hours} ч {minutes} мин. "
                "Если вопрос срочный, дополните уже открытое обращение.",
                "rate_limited",
            )
        ticket = SupportTicket(number=await _new_number(session), user_id=user_id, category=category, subject=_preview(text))
        session.add(ticket)
        await session.flush()
        msg = SupportMessage(
            ticket_id=ticket.id, author_type="user", author_id=user_id, body=text,
            attachment_file_id=attachment[0] if attachment else None, attachment_kind=attachment[1] if attachment else None,
        )
        session.add(msg)
        await session.flush()
        user = await session.get(User, user_id)
    await notify_admins(
        ctx,
        f"🆕 <b>Обращение {ticket.number}</b> · {CATEGORIES[category]}\nОт: {user.display_name} · {user.public_id} · <code>{user_id}</code>\n\n{_escape(text[:700])}",
        ticket,
    )
    return ticket, msg


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def user_tickets(ctx: AppContext, user_id: int) -> list[tuple[SupportTicket, str | None]]:
    async with ctx.session() as session:
        tickets = list(
            (await session.execute(select(SupportTicket).where(SupportTicket.user_id == user_id).order_by(SupportTicket.updated_at.desc()).limit(50))).scalars()
        )
        return [(t, await _last_preview(session, t.id, include_internal=False)) for t in tickets]


async def _last_preview(session, ticket_id: int, *, include_internal: bool) -> str | None:
    stmt = select(SupportMessage.body).where(SupportMessage.ticket_id == ticket_id)
    if not include_internal:
        stmt = stmt.where(SupportMessage.is_internal.is_(False))
    body = await session.scalar(stmt.order_by(SupportMessage.id.desc()).limit(1))
    return _preview(body) if body else None


async def _owned(session, ticket_id: int, user_id: int, *, lock: bool = False) -> SupportTicket:
    stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
    if lock:
        stmt = stmt.with_for_update()
    ticket = await session.scalar(stmt)
    # Same error for "missing" and "someone else's" — no ticket enumeration.
    if ticket is None or ticket.user_id != user_id:
        raise ServiceError("Обращение не найдено.", "not_found")
    return ticket


async def user_thread(ctx: AppContext, user_id: int, ticket_id: int) -> tuple[SupportTicket, list[SupportMessage]]:
    async with ctx.session() as session:
        ticket = await _owned(session, ticket_id, user_id)
        msgs = list(
            (
                await session.execute(
                    select(SupportMessage).where(SupportMessage.ticket_id == ticket.id, SupportMessage.is_internal.is_(False)).order_by(SupportMessage.id)
                )
            ).scalars()
        )
    return ticket, msgs


async def user_message(ctx: AppContext, user_id: int, ticket_id: int, text: str, *, attachment: tuple[str, str] | None = None) -> tuple[SupportTicket, SupportMessage]:
    text = _clean(text, min_len=1)
    async with ctx.session() as session, session.begin():
        ticket = await _owned(session, ticket_id, user_id, lock=True)
        if ticket.status == TicketStatus.CLOSED:
            raise ServiceError("Обращение закрыто. Создайте новое, если вопрос остался.", "closed")
        recent = await session.scalar(
            select(func.count(SupportMessage.id))
            .join(SupportTicket, SupportTicket.id == SupportMessage.ticket_id)
            .where(SupportTicket.user_id == user_id, SupportMessage.author_type == "user", SupportMessage.created_at > utcnow() - timedelta(hours=1))
        )
        if int(recent or 0) >= 60:
            raise ServiceError("Слишком много сообщений. Подождите немного.", "rate_limited")
        msg = SupportMessage(
            ticket_id=ticket.id, author_type="user", author_id=user_id, body=text,
            attachment_file_id=attachment[0] if attachment else None, attachment_kind=attachment[1] if attachment else None,
        )
        session.add(msg)
        if ticket.status in (TicketStatus.WAITING_USER, TicketStatus.RESOLVED):
            ticket.status = TicketStatus.IN_PROGRESS if ticket.assigned_admin_id else TicketStatus.NEW
        ticket.updated_at = utcnow()
        await session.flush()
    await notify_admins(ctx, f"💬 <b>{ticket.number}</b>: новое сообщение пользователя\n\n{_escape(text[:700])}", ticket)
    return ticket, msg


async def user_close(ctx: AppContext, user_id: int, ticket_id: int) -> SupportTicket:
    async with ctx.session() as session, session.begin():
        ticket = await _owned(session, ticket_id, user_id, lock=True)
        if ticket.status != TicketStatus.CLOSED:
            ticket.status, ticket.closed_at, ticket.updated_at = TicketStatus.CLOSED, utcnow(), utcnow()
            session.add(SupportMessage(ticket_id=ticket.id, author_type="system", body="Пользователь закрыл обращение"))
    return ticket


# ---------------- admin side ----------------

async def _admin_ticket(session, ticket_id: int, *, lock: bool = False) -> SupportTicket:
    ticket = await session.get(SupportTicket, ticket_id, with_for_update=lock)
    if ticket is None:
        raise ServiceError("Обращение не найдено.", "not_found")
    return ticket


async def find_by_number(ctx: AppContext, number: str) -> SupportTicket | None:
    number = number.strip().upper()
    if not number.startswith("SUP-"):
        number = f"SUP-{number}"
    async with ctx.session() as session:
        return await session.scalar(select(SupportTicket).where(SupportTicket.number == number))


async def admin_list(ctx: AppContext, admin_id: int, *, status: str = "", category: str = "", assigned: str = "", limit: int = 100) -> list[tuple[SupportTicket, User, str | None]]:
    stmt = select(SupportTicket, User).join(User, User.id == SupportTicket.user_id)
    if status == "open":
        stmt = stmt.where(SupportTicket.status.in_(TicketStatus.OPEN))
    elif status:
        stmt = stmt.where(SupportTicket.status == status)
    if category:
        stmt = stmt.where(SupportTicket.category == category)
    if assigned == "me":
        stmt = stmt.where(SupportTicket.assigned_admin_id == admin_id)
    elif assigned == "unassigned":
        stmt = stmt.where(SupportTicket.assigned_admin_id.is_(None))
    stmt = stmt.order_by(SupportTicket.updated_at.desc()).limit(max(1, min(limit, 300)))
    async with ctx.session() as session:
        rows = (await session.execute(stmt)).all()
        return [(t, u, await _last_preview(session, t.id, include_internal=True)) for t, u in rows]


async def admin_thread(ctx: AppContext, ticket_id: int) -> tuple[SupportTicket, User, list[SupportMessage]]:
    async with ctx.session() as session:
        ticket = await _admin_ticket(session, ticket_id)
        user = await session.get(User, ticket.user_id)
        msgs = list((await session.execute(select(SupportMessage).where(SupportMessage.ticket_id == ticket.id).order_by(SupportMessage.id))).scalars())
    return ticket, user, msgs


async def admin_reply(ctx: AppContext, admin_id: int, ticket_id: int, text: str, *, internal: bool = False, status: str | None = None) -> tuple[SupportTicket, SupportMessage]:
    text = _clean(text, min_len=1)
    if status is not None and status not in TicketStatus.ALL:
        raise ServiceError("Неизвестный статус.", "bad_status")
    async with ctx.session() as session, session.begin():
        ticket = await _admin_ticket(session, ticket_id, lock=True)
        msg = SupportMessage(ticket_id=ticket.id, author_type="admin", author_id=admin_id, body=text, is_internal=internal)
        session.add(msg)
        if ticket.assigned_admin_id is None:
            ticket.assigned_admin_id = admin_id
        new_status = status or (None if internal else TicketStatus.WAITING_USER)
        if new_status and new_status != ticket.status:
            await _change_status(session, ticket, new_status, admin_id)
        elif not internal and ticket.status == TicketStatus.NEW:
            await _change_status(session, ticket, TicketStatus.IN_PROGRESS, admin_id)
        ticket.updated_at = utcnow()
        await session.flush()
        user_id, number = ticket.user_id, ticket.number
    if not internal:
        await ctx.notifier.send(
            user_id,
            f"💬 <b>Ответ поддержки</b> · {number}\n\n{_escape(text)}",
            buttons=[("✍️ Ответить", f"sup:r:{ticket_id}"), ("📂 Открыть обращение", f"sup:v:{ticket_id}")],
        )
    return ticket, msg


async def _change_status(session, ticket: SupportTicket, status: str, admin_id: int) -> None:
    from backend.services import admin as admin_svc

    old = ticket.status
    ticket.status = status
    ticket.updated_at = utcnow()
    ticket.closed_at = utcnow() if status == TicketStatus.CLOSED else None
    session.add(SupportMessage(ticket_id=ticket.id, author_type="system", author_id=admin_id, body=f"Статус: {STATUS_LABELS[status]}"))
    admin_svc.audit(
        session, admin_id=admin_id, source="support", action="support.status", entity_type="support_ticket", entity_id=ticket.id,
        reason=f"обращение {ticket.number}", old={"status": old}, new={"status": status},
    )


async def set_status(ctx: AppContext, admin_id: int, ticket_id: int, status: str) -> SupportTicket:
    if status not in TicketStatus.ALL:
        raise ServiceError("Неизвестный статус.", "bad_status")
    async with ctx.session() as session, session.begin():
        ticket = await _admin_ticket(session, ticket_id, lock=True)
        if ticket.status == status:
            return ticket
        await _change_status(session, ticket, status, admin_id)
        user_id, number = ticket.user_id, ticket.number
    if status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        await ctx.notifier.send(user_id, f"✅ Обращение {number}: {STATUS_LABELS[status].lower()}. Если вопрос остался — просто ответьте в обращении.", buttons=[("📂 Открыть обращение", f"sup:v:{ticket_id}")])
    return ticket


async def assign(ctx: AppContext, admin_id: int, ticket_id: int, target_admin_id: int | None) -> SupportTicket:
    from backend.services import admin as admin_svc

    if target_admin_id is not None and not await roles.is_staff(ctx, target_admin_id):
        raise ServiceError("Назначить можно только администратора.", "not_admin")
    async with ctx.session() as session, session.begin():
        ticket = await _admin_ticket(session, ticket_id, lock=True)
        old = ticket.assigned_admin_id
        ticket.assigned_admin_id = target_admin_id
        ticket.updated_at = utcnow()
        if target_admin_id and ticket.status == TicketStatus.NEW:
            ticket.status = TicketStatus.IN_PROGRESS
        session.add(
            SupportMessage(
                ticket_id=ticket.id, author_type="system", author_id=admin_id, is_internal=True,
                body=f"Ответственный: {target_admin_id or 'не назначен'} (назначил {admin_id})",
            )
        )
        admin_svc.audit(
            session, admin_id=admin_id, source="support", action="support.assign", entity_type="support_ticket", entity_id=ticket.id,
            reason=f"обращение {ticket.number}", old={"assigned": old}, new={"assigned": target_admin_id},
        )
        number = ticket.number
    if target_admin_id and target_admin_id != admin_id:
        await ctx.notifier.send(target_admin_id, f"📌 Вам передано обращение {number}.", buttons=await _admin_buttons(ctx, ticket_id))
    return ticket


async def counts(ctx: AppContext) -> dict[str, int]:
    async with ctx.session() as session:
        new = await session.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.NEW))
        open_ = await session.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.status.in_(TicketStatus.OPEN)))
    return {"support_new": int(new or 0), "support_open": int(open_ or 0)}


async def get_attachment(ctx: AppContext, message_id: int, *, user_id: int | None) -> SupportMessage:
    """user_id=None means admin access."""
    async with ctx.session() as session:
        msg = await session.get(SupportMessage, message_id)
        if msg is None or msg.attachment_file_id is None:
            raise ServiceError("Вложение не найдено.", "not_found")
        if user_id is not None:
            ticket = await session.get(SupportTicket, msg.ticket_id)
            if ticket.user_id != user_id or msg.is_internal:
                raise ServiceError("Вложение не найдено.", "not_found")
    return msg

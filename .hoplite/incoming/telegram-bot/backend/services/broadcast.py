"""Admin messages (one user / everyone) and maintenance notices, sent by the bot through a DB-backed queue.

* Jobs live in the `broadcasts` table; that table is also the journal (who, when, kind, counts, result).
* One sender runs at a time (PostgreSQL advisory lock) inside the background worker, never in a request handler.
* Telegram limits are respected: ~20 messages/s, and `retry_after` from flood control is honoured before retrying.
* Progress (cursor) is committed in small batches, so a restart resumes instead of messaging people twice.
* Text is sent as plain text (no HTML parsing), line breaks preserved.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from backend.models import AppSetting, Broadcast, User
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.broadcast")

LOCK_ID = 7307
MAX_TEXT = 4000
SEND_INTERVAL = 0.05  # 20 msg/s, below Telegram's ~30/s global limit
BATCH = 25
MAX_RETRY_AFTER = 120
MAINTENANCE_KEY = "maintenance_state"

KIND_LABELS = {
    "single": "Одному пользователю",
    "all": "Всем пользователям",
    "maintenance_down": "Технические работы",
    "maintenance_up": "Бот снова работает",
}
STATUS_LABELS = {"queued": "В очереди", "running": "Отправляется", "done": "Завершена", "cancelled": "Остановлена", "failed": "Ошибка"}

TEMPLATE_DOWN = (
    "⚙️ SpirittVPN временно недоступен\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "Мы проводим техническое обновление сервиса. Пожалуйста, подождите немного.\n\n"
    "Мы сообщим, когда всё снова заработает."
)
TEMPLATE_UP = (
    "✅ SpirittVPN снова доступен!\n"
    "━━━━━━━━━━━━━━━━\n\n"
    "Технические работы завершены. Все основные функции восстановлены. Можете продолжать пользоваться ботом."
)
NO_REASON = "без указания причины"


def optional_reason(reason: str | None) -> str:
    """Messages don't require a reason; the audit log (min 3 chars) gets a neutral placeholder instead."""
    reason = " ".join((reason or "").split())[:500]
    return reason if len(reason) >= 3 else (f"{NO_REASON}: {reason}" if reason else NO_REASON)


def clean_text(value: str) -> str:
    value = (value or "").replace("\r\n", "\n").strip()
    if not value:
        raise ServiceError("Введите текст сообщения.", "bad_text")
    if len(value) > MAX_TEXT:
        raise ServiceError(f"Сообщение длиннее {MAX_TEXT} символов.", "bad_text")
    return value


def serialize(b: Broadcast, target_name: str | None = None) -> dict[str, Any]:
    ts = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "id": b.id, "kind": b.kind, "kind_label": KIND_LABELS.get(b.kind, b.kind), "target_user_id": b.target_user_id,
        "target_name": target_name, "text": b.text, "status": b.status, "status_label": STATUS_LABELS.get(b.status, b.status),
        "total": b.total, "sent": b.sent, "failed": b.failed, "blocked": b.blocked, "created_by": b.created_by,
        "created_at": ts(b.created_at), "started_at": ts(b.started_at), "finished_at": ts(b.finished_at), "error": b.error,
    }


def _recipients_query():
    return select(User.id).where(User.is_banned.is_(False))


async def recipients_count(ctx: AppContext) -> int:
    async with ctx.session() as session:
        return int(await session.scalar(select(func.count()).select_from(_recipients_query().subquery())) or 0)


async def find_user(ctx: AppContext, user_id: int) -> User:
    async with ctx.session() as session:
        user = await session.get(User, user_id)
    if user is None:
        raise ServiceError("Пользователь с таким Telegram ID не найден в базе бота.", "user_not_found")
    return user


async def create(
    ctx: AppContext, *, admin_id: int | None, source: str, kind: str, text_: str, reason: str, target_user_id: int | None = None
) -> Broadcast:
    from backend.services import admin as admin_svc

    body = clean_text(text_)
    reason = optional_reason(reason)
    if kind not in KIND_LABELS:
        raise ServiceError("Неизвестный тип рассылки.", "bad_request")
    async with ctx.session() as session, session.begin():
        if kind == "single":
            if target_user_id is None or await session.get(User, target_user_id) is None:
                raise ServiceError("Пользователь с таким Telegram ID не найден в базе бота.", "user_not_found")
            total = 1
        else:
            # Serialise creation of mass jobs so a double click can't queue the same broadcast twice.
            await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_ID + 1})
            busy = await session.scalar(
                select(Broadcast.id).where(Broadcast.kind != "single", Broadcast.status.in_(("queued", "running"))).limit(1)
            )
            if busy:
                raise ServiceError("Другая массовая рассылка ещё отправляется. Дождитесь её завершения или остановите её.", "broadcast_running")
            total = int(await session.scalar(select(func.count()).select_from(_recipients_query().subquery())) or 0)
        b = Broadcast(kind=kind, target_user_id=target_user_id if kind == "single" else None, text=body, total=total,
                      created_by=admin_id, reason=reason)
        session.add(b)
        await session.flush()
        if admin_id:
            admin_svc.audit(
                session, admin_id=admin_id, source=source, action=f"broadcast.{kind}", entity_type="broadcast", entity_id=b.id,
                reason=reason, new={"target_user_id": b.target_user_id, "recipients": total, "chars": len(body)},
            )
    wake(ctx)
    return b


async def cancel(ctx: AppContext, admin_id: int, source: str, broadcast_id: int, reason: str = "") -> Broadcast:
    from backend.services import admin as admin_svc

    reason = optional_reason(reason)
    async with ctx.session() as session, session.begin():
        b = await session.get(Broadcast, broadcast_id, with_for_update=True)
        if b is None:
            raise ServiceError("Рассылка не найдена.", "not_found")
        if b.status not in ("queued", "running"):
            raise ServiceError("Рассылка уже завершена.", "already")
        b.status, b.finished_at = "cancelled", utcnow()
        admin_svc.audit(session, admin_id=admin_id, source=source, action="broadcast.cancel", entity_type="broadcast",
                        entity_id=b.id, reason=reason, new={"sent": b.sent, "total": b.total})
    return b


async def list_recent(ctx: AppContext, limit: int = 30) -> list[dict[str, Any]]:
    async with ctx.session() as session:
        rows = (await session.execute(
            select(Broadcast, User).outerjoin(User, User.id == Broadcast.target_user_id).order_by(Broadcast.id.desc()).limit(limit)
        )).all()
    return [serialize(b, u.display_name if u else None) for b, u in rows]


# ---------------- maintenance ----------------

@dataclass
class MaintenanceState:
    active: bool = False
    auto_restore: bool = False
    restore_text: str | None = None
    announced_at: str | None = None
    announced_by: int | None = None

    def dict(self) -> dict[str, Any]:
        return dict(vars(self))


async def get_maintenance(ctx: AppContext) -> MaintenanceState:
    async with ctx.session() as session:
        row = await session.get(AppSetting, MAINTENANCE_KEY)
    data = row.value if row and isinstance(row.value, dict) else {}
    return MaintenanceState(**{k: data.get(k) for k in MaintenanceState.__dataclass_fields__ if k in data})


async def _put_maintenance(session, state: MaintenanceState) -> None:
    stmt = insert(AppSetting).values(key=MAINTENANCE_KEY, value=state.dict())
    await session.execute(stmt.on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": state.dict()}))


async def announce_maintenance(
    ctx: AppContext, admin_id: int, source: str, text_: str, *, auto_restore: bool, restore_text: str | None, reason: str
) -> tuple[MaintenanceState, Broadcast]:
    restore = (restore_text or "").strip() or None
    if auto_restore:
        restore = clean_text(restore) if restore else TEMPLATE_UP
    b = await create(ctx, admin_id=admin_id, source=source, kind="maintenance_down", text_=text_, reason=reason)
    state = MaintenanceState(True, auto_restore, restore, utcnow().isoformat(), admin_id)
    async with ctx.session() as session, session.begin():
        await _put_maintenance(session, state)
    return state, b


async def restore_now(ctx: AppContext, admin_id: int, source: str, text_: str, reason: str) -> tuple[MaintenanceState, Broadcast]:
    b = await create(ctx, admin_id=admin_id, source=source, kind="maintenance_up", text_=text_, reason=reason)
    state = MaintenanceState()
    async with ctx.session() as session, session.begin():
        await _put_maintenance(session, state)
    return state, b


async def cancel_maintenance(ctx: AppContext, admin_id: int, source: str, reason: str = "") -> MaintenanceState:
    from backend.services import admin as admin_svc

    reason = optional_reason(reason)
    state = MaintenanceState()
    async with ctx.session() as session, session.begin():
        await _put_maintenance(session, state)
        admin_svc.audit(session, admin_id=admin_id, source=source, action="maintenance.cancel", entity_type="maintenance",
                        entity_id=0, reason=reason)
    return state


async def on_startup_verified(ctx: AppContext) -> Broadcast | None:
    """Call only after the bot has started and passed its checks (Telegram + DB).

    Atomically consumes the "auto restore" flag, so two processes or a crash loop can't queue it twice.
    """
    async with ctx.session() as session, session.begin():
        row = await session.get(AppSetting, MAINTENANCE_KEY, with_for_update=True)
        data = row.value if row and isinstance(row.value, dict) else {}
        if not (data.get("active") and data.get("auto_restore")):
            return None
        restore = data.get("restore_text") or TEMPLATE_UP
        await _put_maintenance(session, MaintenanceState())
    log.info("maintenance: bot started successfully, queueing the 'back online' notice")
    return await create(ctx, admin_id=None, source="system", kind="maintenance_up", text_=restore, reason="автоматически после успешного запуска")


# ---------------- sender ----------------

def wake(ctx: AppContext) -> None:
    ev: asyncio.Event | None = ctx.extra.get("broadcast_wake")
    if ev is not None:
        ev.set()


async def _send_one(bot, user_id: int, body: str) -> str:
    """'sent' | 'blocked' | 'failed'. Flood control: waits retry_after and retries the same user."""
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

    for _ in range(5):
        try:
            await bot.send_message(user_id, body, parse_mode=None, disable_web_page_preview=True)
            return "sent"
        except TelegramRetryAfter as exc:
            wait = min(MAX_RETRY_AFTER, int(exc.retry_after) + 1)
            log.warning("flood control: waiting %ss", wait)
            await asyncio.sleep(wait)
        except TelegramForbiddenError:
            return "blocked"  # user blocked the bot / deactivated
        except TelegramBadRequest as exc:
            return "blocked" if "chat not found" in str(exc).lower() else "failed"
        except Exception as exc:  # noqa: BLE001 - network etc.
            log.info("broadcast send to %s failed: %s", user_id, type(exc).__name__)
            await asyncio.sleep(1)
    return "failed"


async def _next_job(ctx: AppContext) -> int | None:
    async with ctx.session() as session:
        return await session.scalar(
            select(Broadcast.id).where(Broadcast.status.in_(("queued", "running"))).order_by(Broadcast.id).limit(1)
        )


async def run_job(ctx: AppContext, broadcast_id: int) -> None:
    bot = getattr(ctx.notifier, "bot", None)
    async with ctx.session() as session, session.begin():
        b = await session.get(Broadcast, broadcast_id, with_for_update=True)
        if b is None or b.status not in ("queued", "running"):
            return
        if bot is None:
            b.status, b.error, b.finished_at = "failed", "Бот не настроен (нет BOT_TOKEN)", utcnow()
            return
        b.status = "running"
        b.started_at = b.started_at or utcnow()
        kind, body, target, cursor = b.kind, b.text, b.target_user_id, b.cursor_user_id
    counts = {"sent": 0, "failed": 0, "blocked": 0}

    async def flush(last_uid: int | None, *, final: bool) -> bool:
        """Persist progress; returns False if an admin cancelled the job meanwhile."""
        async with ctx.session() as session, session.begin():
            b = await session.get(Broadcast, broadcast_id, with_for_update=True)
            b.sent += counts["sent"]
            b.failed += counts["failed"]
            b.blocked += counts["blocked"]
            if last_uid is not None:
                b.cursor_user_id = last_uid
            for k in counts:
                counts[k] = 0
            if b.status == "cancelled":
                return False
            if final:
                b.status, b.finished_at = "done", utcnow()
                log.info("broadcast #%s %s done: sent=%s failed=%s blocked=%s", b.id, b.kind, b.sent, b.failed, b.blocked)
            return True

    if kind == "single":
        counts[await _send_one(bot, target, body)] += 1
        await flush(target, final=True)
        return

    last = cursor
    while True:
        async with ctx.session() as session:
            q = _recipients_query().order_by(User.id).limit(BATCH)
            if last is not None:
                q = q.where(User.id > last)
            ids = list((await session.execute(q)).scalars())
        if not ids:
            await flush(last, final=True)
            return
        for uid in ids:
            counts[await _send_one(bot, uid, body)] += 1
            last = uid
            await asyncio.sleep(SEND_INTERVAL)
        if not await flush(last, final=False):
            log.info("broadcast #%s cancelled by admin", broadcast_id)
            return


async def run_forever(ctx: AppContext, stop: asyncio.Event) -> None:
    """Single sender across processes (advisory lock held on a dedicated connection)."""
    wake_ev = ctx.extra.setdefault("broadcast_wake", asyncio.Event())
    while not stop.is_set():
        try:
            async with ctx.session() as session:
                conn = await session.connection()
                got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_ID})).scalar()
                if got:
                    try:
                        while not stop.is_set() and (job := await _next_job(ctx)) is not None:
                            try:
                                await run_job(ctx, job)
                            except Exception as exc:  # noqa: BLE001
                                log.exception("broadcast #%s crashed", job)
                                async with ctx.session() as s2, s2.begin():
                                    await s2.execute(update(Broadcast).where(Broadcast.id == job).values(
                                        status="failed", error=type(exc).__name__[:500], finished_at=utcnow()))
                    finally:
                        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_ID})
                        await session.commit()
        except Exception:  # noqa: BLE001 - DB hiccup: retry later, never kill the worker
            log.exception("broadcast loop error")
        wake_ev.clear()
        try:
            await asyncio.wait_for(wake_ev.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass

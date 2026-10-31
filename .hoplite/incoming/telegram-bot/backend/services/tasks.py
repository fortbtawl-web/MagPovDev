"""SpirCoin tasks ("subscribe to a channel" etc.).

Verification is per task:
  * telegram — a channel task: the user opens the channel link and claims; membership is NOT checked
    (only the mandatory news channel is, see services/access.py). The value name is kept for DB compatibility.
  * manual   — the claim waits for a moderator/admin decision. Rewards are one per (task, user), within the task budget and the
global emission limits; the unique index on coin_transactions is a second line of defence.
"""
from __future__ import annotations

import logging
import secrets
import re
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Task, TaskClaim, User
from backend.services import coins as coins_svc
from backend.services.access import ChannelCheckError
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.tasks")
CHAT_RE = re.compile(r"^(@[A-Za-z0-9_]{4,32}|-100\d{5,15})$")
URL_RE = re.compile(r"^https://[^\s]{4,290}$")
MAX_MANUAL_ATTEMPTS = 3


class MemberChecker(Protocol):
    async def status(self, chat_id: str, user_id: int) -> tuple[str, bool | None]: ...


class BotMemberChecker:
    """getChatMember for an arbitrary chat (one Bot instance for all tasks)."""

    def __init__(self, bot, timeout: float = 10.0):
        self.bot, self.timeout = bot, timeout

    async def status(self, chat_id: str, user_id: int) -> tuple[str, bool | None]:
        import asyncio

        try:
            member = await asyncio.wait_for(self.bot.get_chat_member(chat_id=chat_id, user_id=user_id), self.timeout)
        except asyncio.TimeoutError as exc:
            raise ChannelCheckError("timeout") from exc
        except Exception as exc:  # noqa: BLE001 - TelegramBadRequest/Forbidden: bot has no access to the member list
            raise ChannelCheckError(f"{type(exc).__name__}: {getattr(exc, 'message', '')}".strip()) from exc
        status = getattr(getattr(member, "status", ""), "value", getattr(member, "status", ""))
        return str(status), getattr(member, "is_member", None)


def _visible(task: Task) -> bool:
    return task.is_active and task.archived_at is None


def _exhausted(task: Task) -> bool:
    return (task.budget_total is not None and task.issued_total + task.reward > task.budget_total) or (
        task.max_completions is not None and task.completions >= task.max_completions
    )


async def list_for_user(ctx: AppContext, user_id: int) -> list[dict[str, Any]]:
    async with ctx.session() as session:
        tasks = (await session.execute(select(Task).where(Task.archived_at.is_(None), Task.is_active.is_(True)).order_by(Task.sort_order, Task.id))).scalars().all()
        claims = {c.task_id: c for c in (await session.execute(select(TaskClaim).where(TaskClaim.user_id == user_id))).scalars()}
    out = []
    for t in tasks:
        c = claims.get(t.id)
        note = None
        if c is not None and c.status == "rewarded":
            status = "rewarded"
        elif c is not None and c.status == "pending":
            status, note = "pending", "Заявка на проверке у модератора."
        elif c is not None and c.status == "rejected":
            status, note = "rejected", c.reason or "Заявка отклонена."
        elif _exhausted(t):
            status, note = "unavailable", "Награды по этому заданию закончились."
        else:
            status = "available"
        out.append({
            "id": t.id, "title": t.title, "description": t.description, "url": t.url, "reward": t.reward,
            "verify": t.verify, "status": status, "note": note,
        })
    return out


@dataclass
class ClaimResult:
    status: str  # rewarded | pending | rejected | not_subscribed
    message: str
    balance: int


async def _grant(session: AsyncSession, ctx: AppContext, task: Task, claim: TaskClaim, *, actor: str) -> int:
    """Within the caller's transaction, with the task row locked."""
    if _exhausted(task):
        raise ServiceError("Награды по этому заданию закончились.", "task_exhausted")
    tx = await coins_svc.apply(
        session, claim.user_id, task.reward, kind="task_reward", actor=actor, reason=task.title, task_id=task.id,
        tz_name=ctx.settings.display_timezone,
    )
    task.issued_total += task.reward
    task.completions += 1
    claim.status, claim.decided_at = "rewarded", utcnow()
    return tx.balance_after


async def claim(ctx: AppContext, user_id: int, task_id: int) -> ClaimResult:
    async with ctx.session() as session:
        task = await session.get(Task, task_id)
        if task is None or not _visible(task):
            raise ServiceError("Задание недоступно.", "task_unavailable")
        user = await session.get(User, user_id)
        if user is None or user.is_banned:
            raise ServiceError("Аккаунт заблокирован. Обратитесь в поддержку.", "banned")
        existing = await session.scalar(select(TaskClaim).where(TaskClaim.task_id == task_id, TaskClaim.user_id == user_id))
        bal = user.coin_balance
    if existing is not None and existing.status == "rewarded":
        return ClaimResult("rewarded", "Награда за это задание уже получена.", bal)
    if existing is not None and existing.status == "pending":
        return ClaimResult("pending", "Заявка уже на проверке. Награда придёт после подтверждения.", bal)

    if task.verify == "telegram":
        # Link tasks (value name kept for DB compatibility): pressing the task button is the completion.
        # Channel tasks: the user follows the link and comes back; membership is not re-checked
        # (only the mandatory news channel is verified, in services/access.py). One reward per user still applies.
        try:
            async with ctx.session() as session, session.begin():
                t = await session.get(Task, task_id, with_for_update=True)
                if not _visible(t):
                    raise ServiceError("Задание недоступно.", "task_unavailable")
                c = await session.scalar(
                    select(TaskClaim).where(TaskClaim.task_id == task_id, TaskClaim.user_id == user_id).with_for_update()
                )
                if c is not None and c.status == "rewarded":
                    return ClaimResult("rewarded", "Награда за это задание уже получена.", await coins_svc.balance(session, user_id))
                if _exhausted(t):
                    raise ServiceError("Награды по этому заданию закончились.", "task_exhausted")
                if c is None:
                    c = TaskClaim(task_id=task_id, user_id=user_id, status="pending")
                    session.add(c)
                    await session.flush()
                t.last_check_error = None
                new_balance = await _grant(session, ctx, t, c, actor="system:telegram")
        except IntegrityError:
            async with ctx.session() as session:
                return ClaimResult("rewarded", "Награда за это задание уже получена.", await coins_svc.balance(session, user_id))
        return ClaimResult("rewarded", f"Награда за переход по ссылке начислена: +{coins_svc.fmt_coins(task.reward)}", new_balance)

    # manual
    async with ctx.session() as session, session.begin():
        t = await session.get(Task, task_id, with_for_update=True)
        if _exhausted(t):
            raise ServiceError("Награды по этому заданию закончились.", "task_exhausted")
        c = await session.scalar(select(TaskClaim).where(TaskClaim.task_id == task_id, TaskClaim.user_id == user_id).with_for_update())
        if c is None:
            session.add(TaskClaim(task_id=task_id, user_id=user_id, status="pending"))
        elif c.status == "rejected":
            if c.attempts >= MAX_MANUAL_ATTEMPTS:
                return ClaimResult("rejected", "Попытки исчерпаны. Если это ошибка, напишите в поддержку.", bal)
            c.status, c.attempts, c.reason, c.decided_at, c.reviewed_by = "pending", c.attempts + 1, None, None, None
    return ClaimResult("pending", "Заявка отправлена на проверку. Награда придёт после подтверждения модератором.", bal)


async def review(ctx: AppContext, staff_id: int, source: str, claim_id: int, *, approve: bool, reason: str) -> TaskClaim:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        c = await session.get(TaskClaim, claim_id, with_for_update=True)
        if c is None:
            raise ServiceError("Заявка не найдена.", "not_found")
        if c.status != "pending":
            raise ServiceError("Заявка уже обработана.", "already")
        t = await session.get(Task, c.task_id, with_for_update=True)
        if approve and not _visible(t):
            raise ServiceError("Задание снято с публикации — награду по нему выдать нельзя. Отклоните заявку.", "task_unavailable")
        if approve:
            await _grant(session, ctx, t, c, actor=f"admin:{staff_id}")
        else:
            c.status, c.decided_at = "rejected", utcnow()
        c.reason, c.reviewed_by = reason, staff_id
        admin_svc.audit(
            session, admin_id=staff_id, source=source, action="task.claim_approve" if approve else "task.claim_reject",
            entity_type="task_claim", entity_id=c.id, reason=reason, new={"task": t.code, "user_id": c.user_id, "reward": t.reward if approve else 0},
        )
        user_id, title, reward = c.user_id, t.title, t.reward
    if approve:
        async with ctx.session() as session:
            bal = await coins_svc.balance(session, user_id)
        text = reward_message(reward, bal, title)
    else:
        import html as _html

        text = (f"✖️ <b>Задание не подтверждено</b>\n<i>{_html.escape(title)}</i>\n━━━━━━━━━━━━━━━━\n\n"
                f"Причина: {_html.escape(reason)}\n\nВы можете выполнить его ещё раз и отправить повторно.")
    await ctx.notifier.send(user_id, text)
    return c


# ---------- admin CRUD ----------

FIELDS = ("title", "description", "url", "reward", "verify", "chat_id", "is_active", "budget_total", "max_completions", "sort_order")


def _validate(values: dict[str, Any], current: Task | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in FIELDS:
        if k not in values:
            continue
        v = values[k]
        if k in ("title",):
            v = " ".join(str(v or "").split())
            if not 2 <= len(v) <= 120:
                raise ServiceError("Название: от 2 до 120 символов.", "bad_value")
        elif k == "description":
            v = str(v or "").strip()[:1000]
        elif k == "url":
            v = str(v or "").strip()
            if not URL_RE.match(v):
                raise ServiceError("Ссылка должна начинаться с https://", "bad_value")
        elif k == "reward":
            if not isinstance(v, int) or not 1 <= v <= 1_000_000:
                raise ServiceError("Награда: целое число от 1 до 1 000 000.", "bad_value")
        elif k == "verify":
            if v not in Task.VERIFY:
                raise ServiceError("Способ проверки: telegram или manual.", "bad_value")
        elif k == "chat_id":
            v = (str(v).strip() or None) if v is not None else None
            if v is not None and not CHAT_RE.match(v):
                raise ServiceError("Чат для проверки: @username канала или числовой ID (-100…).", "bad_value")
        elif k == "is_active":
            v = bool(v)
        elif k in ("budget_total", "max_completions"):
            if v is not None and (not isinstance(v, int) or v < 1):
                raise ServiceError("Лимит должен быть положительным числом или пустым.", "bad_value")
        elif k == "sort_order":
            v = int(v or 0)
        out[k] = v
    verify = out.get("verify", current.verify if current else None)
    chat = out.get("chat_id", current.chat_id if current else None)
    del verify, chat  # link tasks: the channel is optional, kept for reference only
    return out


def task_dict(t: Task, pending: int = 0) -> dict[str, Any]:
    return {
        "id": t.id, "code": t.code, "title": t.title, "description": t.description, "url": t.url, "reward": t.reward,
        "verify": t.verify, "chat_id": t.chat_id, "is_active": t.is_active, "archived": t.archived_at is not None,
        "budget_total": t.budget_total, "issued_total": t.issued_total, "max_completions": t.max_completions,
        "completions": t.completions, "pending": pending, "sort_order": t.sort_order, "last_check_error": t.last_check_error,
    }


async def admin_list(ctx: AppContext, *, include_archived: bool = True) -> list[dict[str, Any]]:
    async with ctx.session() as session:
        stmt = select(Task).order_by(Task.archived_at.is_not(None), Task.sort_order, Task.id)
        if not include_archived:
            stmt = stmt.where(Task.archived_at.is_(None))
        tasks = (await session.execute(stmt)).scalars().all()
        pending = dict((await session.execute(select(TaskClaim.task_id, func.count()).where(TaskClaim.status == "pending").group_by(TaskClaim.task_id))).all())
    return [task_dict(t, int(pending.get(t.id, 0))) for t in tasks]


async def create(ctx: AppContext, admin_id: int, source: str, values: dict[str, Any], reason: str) -> Task:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    for req in ("title", "url", "reward", "verify"):
        if req not in values:
            raise ServiceError(f"Не заполнено поле {req}.", "bad_value")
    data = _validate(values)
    async with ctx.session() as session, session.begin():
        code = str(values.get("code") or "").strip().lower() or f"task_{secrets.token_hex(4)}"
        if not re.match(r"^[a-z0-9_]{2,32}$", code):
            raise ServiceError("Код задания: латиница, цифры и _ (2–32 символа).", "bad_value")
        if await session.scalar(select(Task.id).where(Task.code == code)):
            raise ServiceError("Задание с таким кодом уже есть.", "already")
        t = Task(code=code, **data)
        session.add(t)
        await session.flush()
        admin_svc.audit(session, admin_id=admin_id, source=source, action="task.create", entity_type="task", entity_id=t.id, reason=reason, new=task_dict(t))
    return t


async def update(ctx: AppContext, admin_id: int, source: str, task_id: int, values: dict[str, Any], reason: str) -> Task:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        t = await session.get(Task, task_id, with_for_update=True)
        if t is None:
            raise ServiceError("Задание не найдено.", "not_found")
        if t.archived_at is not None:
            raise ServiceError("Задание в архиве — его нельзя изменить.", "archived")
        old = task_dict(t)
        data = _validate(values, t)
        if data.get("budget_total") is not None and data["budget_total"] < t.issued_total:
            raise ServiceError(f"Бюджет не может быть меньше уже выданного ({t.issued_total}).", "bad_value")
        if data.get("max_completions") is not None and data["max_completions"] < t.completions:
            raise ServiceError(f"Лимит не может быть меньше уже выполненных ({t.completions}).", "bad_value")
        for k, v in data.items():
            setattr(t, k, v)
        if "verify" in data or "chat_id" in data:
            t.last_check_error = None
        await session.flush()
        admin_svc.audit(session, admin_id=admin_id, source=source, action="task.update", entity_type="task", entity_id=t.id, reason=reason, old=old, new=task_dict(t))
    return t


async def archive(ctx: AppContext, admin_id: int, source: str, task_id: int, reason: str) -> Task:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        t = await session.get(Task, task_id, with_for_update=True)
        if t is None:
            raise ServiceError("Задание не найдено.", "not_found")
        if t.archived_at is None:
            t.archived_at, t.is_active = utcnow(), False
            admin_svc.audit(session, admin_id=admin_id, source=source, action="task.archive", entity_type="task", entity_id=t.id, reason=reason)
    return t


async def claims_list(ctx: AppContext, *, status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
    async with ctx.session() as session:
        stmt = (
            select(TaskClaim, Task, User).join(Task, Task.id == TaskClaim.task_id).join(User, User.id == TaskClaim.user_id)
            .order_by(TaskClaim.created_at.desc()).limit(max(1, min(limit, 300)))
        )
        if status:
            stmt = stmt.where(TaskClaim.status == status)
        rows = (await session.execute(stmt)).all()
    return [
        {"id": c.id, "task_id": t.id, "task_title": t.title, "user_id": u.id, "user_name": u.display_name, "status": c.status,
         "reason": c.reason, "created_at": c.created_at.isoformat() if c.created_at else None}
        for c, t, u in rows
    ]


async def user_claims(session: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = (await session.execute(select(TaskClaim, Task).join(Task, Task.id == TaskClaim.task_id).where(TaskClaim.user_id == user_id).order_by(TaskClaim.id.desc()))).all()
    return [{"id": c.id, "task_title": t.title, "status": c.status, "reward": t.reward, "reason": c.reason,
             "created_at": c.created_at.isoformat() if c.created_at else None} for c, t in rows]


# ---------- link tasks: signed click URL + reward message ----------

def click_sig(secret: str, task_id: int, user_id: int) -> str:
    """Binds the click link to one user and one task, so nobody can claim for someone else."""
    import base64
    import hashlib
    import hmac

    mac = hmac.new(secret.encode(), f"task-click:{task_id}:{user_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:12]).decode().rstrip("=")


def click_url(ctx: AppContext, base: str, task_id: int, user_id: int) -> str | None:
    secret = ctx.settings.session_secret.get_secret_value()
    if not base.startswith("https://") or len(secret) < 32:
        return None
    return f"{base.rstrip('/')}/api/go/{task_id}/{user_id}/{click_sig(secret, task_id, user_id)}"


def reward_message(reward: int, balance: int, title: str | None = None) -> str:
    import html

    line = "━━━━━━━━━━━━━━━━"
    head = "🎉 <b>Задание выполнено!</b>"
    if title:
        head += f"\n<i>{html.escape(title)}</i>"
    return (
        f"{head}\n{line}\n\n"
        f"💰 На ваш баланс начислено: <b>+{coins_svc.fmt_coins(reward)}</b>\n"
        "Спасибо за участие в SpirittVPN!\n\n"
        f"{line}\n<b>Текущий баланс:</b> {coins_svc.fmt_coins(balance)}"
    )

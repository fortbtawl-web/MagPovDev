"""Test subscriptions by secret code: once per account (across all codes), separate from the regular trial.

Rules (all checked on the server, under row locks):
  * the code exists, is active, not archived, inside its start/end window and below its activation limit;
  * the user has never received a code subscription (unique trial_code_activations.user_id);
  * the user has no current subscription, and — unless the owner enables `trial_code_after_trial` — has not
    used the regular trial either (the regular trial in turn refuses users who already used a code);
  * the activation counts only after the VPN node confirmed the client; on failure the code slot is returned.
Wrong, disabled, expired and exhausted codes all get the same neutral answer, and failed guesses are rate limited.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.models import Subscription, SubStatus, TrialActivation, TrialCode, TrialCodeActivation, User, VpnOperation
from backend.services import devices as dev_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_dt, utcnow

log = logging.getLogger("spiritt.trial_codes")
CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
NEUTRAL = "Код не найден или больше не действует."
MAX_FAILS, FAIL_WINDOW = 5, 15 * 60
_fails: dict[int, deque[float]] = defaultdict(deque)


def normalize(code: str | None) -> str:
    return re.sub(r"\s+", "", (code or "")).upper().lstrip("@")


def status_of(c: TrialCode, now: datetime | None = None) -> str:
    now = now or utcnow()
    if c.archived_at is not None:
        return "archived"
    if not c.is_active:
        return "disabled"
    if c.ends_at is not None and c.ends_at <= now:
        return "expired"
    if c.max_activations is not None and c.activations >= c.max_activations:
        return "exhausted"
    if c.starts_at is not None and c.starts_at > now:
        return "scheduled"
    return "active"


def _too_many_fails(user_id: int) -> bool:
    q, now = _fails[user_id], time.monotonic()
    while q and now - q[0] > FAIL_WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def _fail(user_id: int) -> ServiceError:
    _fails[user_id].append(time.monotonic())
    return ServiceError(NEUTRAL, "bad_code")


@dataclass
class CodeInfo:
    available: bool
    reason: str | None
    used: bool
    expires_at: datetime | None


async def info(ctx: AppContext, user_id: int) -> CodeInfo:
    async with ctx.session() as session:
        act = await session.scalar(select(TrialCodeActivation).where(TrialCodeActivation.user_id == user_id))
        if act is not None and act.status == "active":
            return CodeInfo(False, "Тестовая подписка по коду уже получена.", True, act.expires_at)
        if act is not None and act.status == "pending":
            return CodeInfo(False, "Тестовая подписка уже активируется, подождите немного.", False, None)
        user = await session.get(User, user_id)
        if user is not None and user.is_banned:
            return CodeInfo(False, "Аккаунт заблокирован.", False, None)
        if await subs_svc.get_current(session, user_id):
            return CodeInfo(False, "У вас уже есть подписка.", False, None)
        if not await settings_svc.get(session, "trial_code_after_trial"):
            trial = await session.scalar(select(TrialActivation.status).where(TrialActivation.user_id == user_id))
            if trial == "active":
                return CodeInfo(False, "Тестовая подписка по коду доступна только тем, кто ещё не получал пробный период.", False, None)
    return CodeInfo(True, None, False, None)


@dataclass
class Activation:
    device: Any
    days: int
    expires_at: datetime

    @property
    def message(self) -> str:
        return f"🎉 Код принят! Тестовая подписка SpiritVPN активирована на {self.days} дн. (до {fmt_dt(self.expires_at)}). Приятного использования!"


async def activate(ctx: AppContext, user_id: int, raw_code: str) -> Activation:
    if _too_many_fails(user_id):
        raise ServiceError("Слишком много неверных попыток. Попробуйте через 15 минут.", "too_many_attempts")
    code = normalize(raw_code)
    if not CODE_RE.match(code):
        raise _fail(user_id)
    state = await info(ctx, user_id)
    if not state.available:
        raise ServiceError(state.reason or "Тестовая подписка недоступна.", "already_used" if state.used else "unavailable")

    try:
        async with ctx.session() as session, session.begin():
            # Same lock as the regular trial: the two can never be granted in parallel for one user.
            await session.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
            act = await session.scalar(select(TrialCodeActivation).where(TrialCodeActivation.user_id == user_id).with_for_update())
            if act is not None and act.status != "failed":
                raise ServiceError("Тестовая подписка по коду уже получена.", "already_used")
            if await subs_svc.get_current(session, user_id):
                raise ServiceError("У вас уже есть подписка.", "has_subscription")
            if not await settings_svc.get(session, "trial_code_after_trial"):
                trial = await session.scalar(select(TrialActivation.status).where(TrialActivation.user_id == user_id))
                if trial in ("active", "pending"):
                    raise ServiceError("Тестовая подписка по коду доступна только тем, кто ещё не получал пробный период.", "trial_used")
            tc = await session.scalar(select(TrialCode).where(TrialCode.code == code).with_for_update())
            if tc is None or status_of(tc) != "active":
                raise _fail(user_id)
            node = await dev_svc.get_node(session, None)
            tc.activations += 1  # reserved now, returned if the VPN side fails
            sub = Subscription(user_id=user_id, plan_code="trial_code", status=SubStatus.PENDING, device_limit=1, source="trial")
            session.add(sub)
            await session.flush()
            if act is None:
                act = TrialCodeActivation(code_id=tc.id, user_id=user_id, status="pending", days=tc.days, subscription_id=sub.id)
                session.add(act)
            else:
                act.code_id, act.status, act.days, act.subscription_id, act.error = tc.id, "pending", tc.days, sub.id, None
            device = await dev_svc.reserve_device(session, sub, node, "Тестовое устройство", allow_pending_sub=True)
            code_id, sub_id, device_id, days = tc.id, sub.id, device.id, tc.days
    except IntegrityError as exc:  # parallel request won the race for this user or the last slot of the code
        raise ServiceError("Запрос уже обрабатывается или код больше не действует. Обновите экран.", "conflict") from exc

    expires = utcnow() + timedelta(days=days)
    result = await dev_svc.provision_device(ctx, device_id, expires_at=expires, actor="user")

    from backend.services import admin as admin_svc

    async with ctx.session() as session, session.begin():
        sub = await session.get(Subscription, sub_id, with_for_update=True)
        act = await session.scalar(select(TrialCodeActivation).where(TrialCodeActivation.user_id == user_id).with_for_update())
        tc = await session.get(TrialCode, code_id, with_for_update=True)
        if result.confirmed:
            now = utcnow()
            sub.status, sub.starts_at, sub.expires_at = SubStatus.ACTIVE, now, expires
            act.status, act.activated_at, act.expires_at = "active", now, expires
            await session.flush()
            subs_svc.log_event(session, sub, "created", actor="user", old_status=SubStatus.PENDING, days=days, comment="trial code")
            admin_svc.audit(session, admin_id=user_id, source="user", action="trial_code.activate", entity_type="trial_code",
                            entity_id=code_id, reason="активация пользователем", new={"user_id": user_id, "days": days, "expires_at": expires})
            return Activation(result.device, days, expires)
        sub.status = SubStatus.FAILED
        act.status, act.error = "failed", (result.error or "")[:500]
        tc.activations = max(0, tc.activations - 1)
        device = await session.get(type(result.device), device_id, with_for_update=True)
        if device.status == "pending":
            device.status = "failed"
            for op in (await session.execute(select(VpnOperation).where(VpnOperation.device_id == device_id, VpnOperation.status == "pending"))).scalars():
                op.status, op.last_error = "failed", "trial code aborted"
            await dev_svc.record_op(session, device=device, node_id=device.node_id, op_type="revoke", status="pending", error="cleanup after failed trial code")
    raise ServiceError("VPN-сервер не подтвердил создание подключения. Код не израсходован — попробуйте позже.", "vpn_failed")


# ---------- admin ----------

def code_dict(c: TrialCode) -> dict[str, Any]:
    from backend.api.serializers import ts

    return {
        "id": c.id, "code": c.code, "name": c.name, "description": c.description, "note": c.note, "days": c.days,
        "max_activations": c.max_activations, "activations": c.activations, "starts_at": ts(c.starts_at), "ends_at": ts(c.ends_at),
        "is_active": c.is_active, "archived": c.archived_at is not None, "status": status_of(c),
        "created_at": ts(c.created_at), "updated_at": ts(c.updated_at),
    }


def _aware(v: datetime | None) -> datetime | None:
    return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


def _validate(values: dict[str, Any], current: TrialCode | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in values:
        name = " ".join(str(values["name"] or "").split())
        if not 1 <= len(name) <= 120:
            raise ServiceError("Название: от 1 до 120 символов.", "bad_value")
        out["name"] = name
    if "description" in values:
        out["description"] = str(values["description"] or "").strip()[:1000]
    if "note" in values:
        out["note"] = (str(values["note"] or "").strip()[:1000]) or None
    if "days" in values:
        d = values["days"]
        if not isinstance(d, int) or not 1 <= d <= 30:
            raise ServiceError("Длительность: от 1 до 30 дней.", "bad_value")
        out["days"] = d
    if "max_activations" in values:
        m = values["max_activations"]
        used = current.activations if current else 0
        if m is not None and (not isinstance(m, int) or m < max(1, used) or m > 1_000_000):
            raise ServiceError(f"Лимит активаций: от {max(1, used)} или пусто (без лимита).", "bad_value")
        out["max_activations"] = m
    for k in ("starts_at", "ends_at"):
        if k in values:
            out[k] = _aware(values[k])
    if "is_active" in values:
        out["is_active"] = bool(values["is_active"])
    starts = out.get("starts_at", current.starts_at if current else None)
    ends = out.get("ends_at", current.ends_at if current else None)
    if starts and ends and ends <= starts:
        raise ServiceError("Дата окончания должна быть позже даты начала.", "bad_date")
    return out


def _generate(length: int, prefix: str = "") -> str:
    return prefix + "".join(secrets.choice(ALPHABET) for _ in range(length))


async def create(ctx: AppContext, admin_id: int, source: str, values: dict[str, Any], reason: str) -> TrialCode:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    if not values.get("name"):
        raise ServiceError("Укажите название кода.", "bad_value")
    data = _validate({"days": 5, "is_active": True, **values})
    code = normalize(values.get("code")) or _generate(8)
    if not CODE_RE.match(code):
        raise ServiceError("Код: латиница, цифры, «_» или «-», от 3 до 32 символов.", "bad_code")
    async with ctx.session() as session, session.begin():
        if await session.scalar(select(TrialCode.id).where(TrialCode.code == code)):
            raise ServiceError("Такой код уже существует.", "duplicate")
        c = TrialCode(code=code, created_by=admin_id, **data)
        session.add(c)
        await session.flush()
        await session.refresh(c)
        admin_svc.audit(session, admin_id=admin_id, source=source, action="trial_code.create", entity_type="trial_code", entity_id=c.id, reason=reason,
                        new={k: v for k, v in code_dict(c).items() if k != "code"} | {"code": code[:2] + "***"})
    return c


async def bulk_create(ctx: AppContext, admin_id: int, source: str, *, count: int, length: int, prefix: str, values: dict[str, Any], reason: str) -> list[TrialCode]:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    if not 1 <= count <= 200:
        raise ServiceError("Количество: от 1 до 200.", "bad_value")
    if not 6 <= length <= 16:
        raise ServiceError("Длина: от 6 до 16 символов.", "bad_value")
    prefix = normalize(prefix)
    if prefix and (not re.match(r"^[A-Z0-9_-]{1,12}$", prefix) or len(prefix) + length > 32):
        raise ServiceError("Префикс: латиница/цифры, до 12 символов, вместе с кодом — до 32.", "bad_value")
    if not values.get("name"):
        raise ServiceError("Укажите название партии.", "bad_value")
    data = _validate({"days": 5, "is_active": True, **values})
    created: list[TrialCode] = []
    async with ctx.session() as session, session.begin():
        taken = set((await session.execute(select(TrialCode.code).where(TrialCode.code.like(f"{prefix}%")))).scalars()) if prefix else set()
        seen: set[str] = set()
        while len(created) < count:
            code = _generate(length, prefix)
            if code in seen or code in taken or await session.scalar(select(TrialCode.id).where(TrialCode.code == code)):
                continue
            seen.add(code)
            c = TrialCode(code=code, created_by=admin_id, **data)
            session.add(c)
            created.append(c)
        await session.flush()
        for c in created:
            await session.refresh(c)
        admin_svc.audit(session, admin_id=admin_id, source=source, action="trial_code.bulk_create", entity_type="trial_code",
                        entity_id=created[0].id, reason=reason, new={"count": count, "name": data.get("name"), "days": data.get("days")})
    return created


async def update(ctx: AppContext, admin_id: int, source: str, code_id: int, values: dict[str, Any], reason: str) -> TrialCode:
    """Only future activations are affected; subscriptions already granted keep their own end date."""
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    values = {k: v for k, v in values.items() if k in ("name", "description", "note", "days", "max_activations", "starts_at", "ends_at", "is_active")}
    if not values:
        raise ServiceError("Нет изменений.", "no_changes")
    async with ctx.session() as session, session.begin():
        c = await session.get(TrialCode, code_id, with_for_update=True)
        if c is None:
            raise ServiceError("Код не найден.", "not_found")
        if c.archived_at is not None:
            raise ServiceError("Код в архиве — изменить нельзя.", "archived")
        old = code_dict(c)
        for k, v in _validate(values, c).items():
            setattr(c, k, v)
        await session.flush()
        await session.refresh(c)
        admin_svc.audit(session, admin_id=admin_id, source=source, action="trial_code.update", entity_type="trial_code", entity_id=c.id, reason=reason,
                        old={k: v for k, v in old.items() if k != "code"}, new={k: v for k, v in code_dict(c).items() if k != "code"})
    return c


async def archive(ctx: AppContext, admin_id: int, source: str, code_id: int, reason: str) -> TrialCode:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        c = await session.get(TrialCode, code_id, with_for_update=True)
        if c is None:
            raise ServiceError("Код не найден.", "not_found")
        if c.archived_at is None:
            c.archived_at, c.is_active = utcnow(), False
            admin_svc.audit(session, admin_id=admin_id, source=source, action="trial_code.archive", entity_type="trial_code", entity_id=c.id, reason=reason)
            await session.flush()
            await session.refresh(c)
    return c


async def delete(ctx: AppContext, admin_id: int, source: str, code_id: int, reason: str) -> None:
    from backend.services import admin as admin_svc

    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        c = await session.get(TrialCode, code_id, with_for_update=True)
        if c is None:
            raise ServiceError("Код не найден.", "not_found")
        if await session.scalar(select(func.count(TrialCodeActivation.id)).where(TrialCodeActivation.code_id == code_id)):
            raise ServiceError("По коду уже были активации — удалить нельзя, используйте архив.", "has_activations")
        admin_svc.audit(session, admin_id=admin_id, source=source, action="trial_code.delete", entity_type="trial_code", entity_id=c.id, reason=reason, old={"name": c.name})
        await session.delete(c)


async def admin_list(ctx: AppContext, *, q: str = "", status: str = "all", sort: str = "new") -> list[TrialCode]:
    async with ctx.session() as session:
        stmt = select(TrialCode)
        q = q.strip()
        if q:
            like = f"%{q}%"
            stmt = stmt.where(TrialCode.name.ilike(like) | TrialCode.code.ilike(like.upper()))
        stmt = stmt.order_by(TrialCode.created_at.asc() if sort == "old" else TrialCode.created_at.desc()).limit(1000)
        items = list((await session.execute(stmt)).scalars())
    if status == "all":
        return [c for c in items if c.archived_at is None]
    return [c for c in items if status_of(c) == status]


async def activations(ctx: AppContext, code_id: int) -> list[dict[str, Any]]:
    from backend.api.serializers import ts

    async with ctx.session() as session:
        rows = (await session.execute(
            select(TrialCodeActivation, User).join(User, User.id == TrialCodeActivation.user_id)
            .where(TrialCodeActivation.code_id == code_id).order_by(TrialCodeActivation.created_at.desc())
        )).all()
    return [{"user_id": u.id, "name": u.display_name, "username": u.username, "status": a.status, "days": a.days,
             "activated_at": ts(a.activated_at), "expires_at": ts(a.expires_at)} for a, u in rows]

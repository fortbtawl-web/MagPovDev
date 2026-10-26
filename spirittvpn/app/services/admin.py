"""Admin operations. Every function re-checks permissions server-side and writes the audit log."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import (
    AuditLog,
    Order,
    OrderStatus,
    Plan,
    PromoActivation,
    PromoCode,
    PromoKind,
    Subscription,
    SubStatus,
    TrialActivation,
    User,
)
from app.services import settings_store
from app.services.orders import user_orders
from app.services.promos import normalize_code, user_activations
from app.services.users import extend_subscription, get_subscription, get_user

log = logging.getLogger("spirittvpn.admin")


class AdminPermissionError(PermissionError):
    pass


class AdminInputError(ValueError):
    pass


def require_admin(settings: Settings, telegram_id: int | None) -> int:
    if not settings.is_admin(telegram_id):
        log.warning("denied admin action for tg=%s", telegram_id)
        raise AdminPermissionError
    assert telegram_id is not None
    return telegram_id


async def audit(session: AsyncSession, admin_id: int, action: str, target: str | None = None, **details) -> None:
    session.add(
        AuditLog(
            admin_telegram_id=admin_id,
            action=action,
            target=target,
            details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
        )
    )


@dataclass(frozen=True)
class Stats:
    users_total: int
    users_day: int
    users_month: int
    active_subs: int
    trials_used: int
    paid_orders: int
    revenue: dict[str, int]
    active_promos: int


async def stats(session: AsyncSession, settings: Settings, admin_id: int, now: datetime) -> Stats:
    require_admin(settings, admin_id)
    count = lambda stmt: session.scalar(stmt)  # noqa: E731
    revenue_rows = await session.execute(
        select(Order.currency, func.sum(Order.amount))
        .where(Order.status == OrderStatus.PAID, Order.is_test.is_(False))
        .group_by(Order.currency)
    )
    return Stats(
        users_total=await count(select(func.count(User.id))) or 0,
        users_day=await count(select(func.count(User.id)).where(User.created_at >= now - timedelta(days=1))) or 0,
        users_month=await count(select(func.count(User.id)).where(User.created_at >= now - timedelta(days=30))) or 0,
        active_subs=await count(
            select(func.count(Subscription.id)).where(Subscription.status == SubStatus.ACTIVE, Subscription.expires_at > now)
        )
        or 0,
        trials_used=await count(select(func.count(TrialActivation.id))) or 0,
        paid_orders=await count(
            select(func.count(Order.id)).where(Order.status == OrderStatus.PAID, Order.is_test.is_(False))
        )
        or 0,
        revenue={cur: int(total or 0) for cur, total in revenue_rows.all()},
        active_promos=await count(
            select(func.count(PromoCode.id)).where(
                PromoCode.is_active,
                PromoCode.is_archived.is_(False),
                (PromoCode.valid_until.is_(None)) | (PromoCode.valid_until > now),
            )
        )
        or 0,
    )


@dataclass
class UserCard:
    user: User
    subscription: Subscription | None
    orders: list[Order]
    activations: list[tuple[PromoActivation, PromoCode]]
    trial_used: bool


async def user_card(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int) -> UserCard | None:
    require_admin(settings, admin_id)
    user = await get_user(session, telegram_id)
    if user is None:
        return None
    trial = await session.scalar(select(TrialActivation.id).where(TrialActivation.user_id == user.id))
    return UserCard(
        user, await get_subscription(session, user.id), await user_orders(session, user.id, 10),
        await user_activations(session, user.id), trial is not None,
    )


async def manual_extend(
    session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, days: int, reason: str, now: datetime
) -> Subscription:
    require_admin(settings, admin_id)
    if not 1 <= days <= 3650:
        raise AdminInputError("Количество дней: 1–3650")
    if not reason.strip():
        raise AdminInputError("Укажи причину продления")
    user = await get_user(session, telegram_id)
    if user is None:
        raise AdminInputError("Пользователь не найден")
    sub = await extend_subscription(session, user, days, now)
    await audit(session, admin_id, "manual_extend", str(telegram_id), days=days, reason=reason.strip()[:200],
                new_expires_at=sub.expires_at.isoformat())
    await session.commit()
    return sub


async def set_blocked(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, blocked: bool) -> User:
    require_admin(settings, admin_id)
    if settings.is_admin(telegram_id):
        raise AdminInputError("Нельзя заблокировать администратора")
    user = await get_user(session, telegram_id)
    if user is None:
        raise AdminInputError("Пользователь не найден")
    user.is_blocked = blocked
    await audit(session, admin_id, "block" if blocked else "unblock", str(telegram_id))
    await session.commit()
    return user


async def set_user_group(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, group: str | None) -> User:
    require_admin(settings, admin_id)
    user = await get_user(session, telegram_id)
    if user is None:
        raise AdminInputError("Пользователь не найден")
    user.group = (group or "").strip()[:32] or None
    await audit(session, admin_id, "set_group", str(telegram_id), group=user.group)
    await session.commit()
    return user


# ---------- plans ----------

def _parse_kv(args: list[str]) -> dict[str, str]:
    out = {}
    for a in args:
        if "=" not in a:
            raise AdminInputError(f"Ожидается ключ=значение, получено: {a}")
        k, v = a.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _int(v: str, name: str, lo: int = 0, hi: int = 10**9) -> int:
    try:
        n = int(v)
    except ValueError:
        raise AdminInputError(f"{name}: нужно целое число") from None
    if not lo <= n <= hi:
        raise AdminInputError(f"{name}: допустимо {lo}–{hi}")
    return n


def _bool(v: str) -> bool:
    if v.lower() in {"1", "on", "true", "да"}:
        return True
    if v.lower() in {"0", "off", "false", "нет"}:
        return False
    raise AdminInputError("Ожидается on/off")


def _date(v: str) -> datetime | None:
    from datetime import UTC

    if v.lower() in {"", "none", "-"}:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise AdminInputError("Дата в формате ГГГГ-ММ-ДД") from None


async def list_plans(session: AsyncSession, settings: Settings, admin_id: int) -> list[Plan]:
    require_admin(settings, admin_id)
    return list((await session.scalars(select(Plan).order_by(Plan.sort_order, Plan.id))).all())


async def create_plan(
    session: AsyncSession, settings: Settings, admin_id: int, code: str, days: int, price: int, title: str
) -> Plan:
    require_admin(settings, admin_id)
    code = code.strip().lower()
    if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 32:
        raise AdminInputError("Код тарифа: латиница/цифры, до 32 символов")
    if await session.scalar(select(Plan.id).where(Plan.code == code)):
        raise AdminInputError("Тариф с таким кодом уже есть")
    plan = Plan(
        code=code, title=title.strip()[:128] or code, description="", duration_days=_int(str(days), "days", 1, 3650),
        price=_int(str(price), "price", 1), currency=settings.payment_currency, is_active=False, sort_order=100,
    )
    session.add(plan)
    await audit(session, admin_id, "plan_create", code, days=days, price=price)
    await session.commit()
    return plan


async def update_plan(session: AsyncSession, settings: Settings, admin_id: int, code: str, args: list[str]) -> Plan:
    require_admin(settings, admin_id)
    plan = await session.scalar(select(Plan).where(Plan.code == code.strip().lower()))
    if plan is None:
        raise AdminInputError("Тариф не найден")
    changes = _parse_kv(args)
    for key, value in changes.items():
        if key == "price":
            plan.price = _int(value, "price", 1)
        elif key == "days":
            plan.duration_days = _int(value, "days", 1, 3650)
        elif key == "title":
            plan.title = value.replace("_", " ")[:128]
        elif key == "desc":
            plan.description = value.replace("_", " ")[:500]
        elif key == "active":
            plan.is_active = _bool(value)
        elif key == "sort":
            plan.sort_order = _int(value, "sort")
        else:
            raise AdminInputError(f"Неизвестное поле: {key}")
    if plan.is_active and plan.price <= 0:
        raise AdminInputError("Нельзя включить тариф без цены")
    await audit(session, admin_id, "plan_update", plan.code, **changes)
    await session.commit()
    return plan


# ---------- promo codes ----------

async def list_promos(session: AsyncSession, settings: Settings, admin_id: int, include_archived: bool = False) -> list[PromoCode]:
    require_admin(settings, admin_id)
    stmt = select(PromoCode).order_by(PromoCode.created_at.desc()).limit(50)
    if not include_archived:
        stmt = stmt.where(PromoCode.is_archived.is_(False))
    return list((await session.scalars(stmt)).all())


def _apply_promo_fields(promo: PromoCode, fields: dict[str, str]) -> None:
    for key, value in fields.items():
        if key == "max":
            promo.max_activations = None if value.lower() in {"none", "-", "0"} else _int(value, "max", 1)
        elif key == "until":
            promo.valid_until = _date(value)
        elif key == "from":
            d = _date(value)
            promo.valid_from = d - timedelta(days=1) + timedelta(seconds=1) if d else None
        elif key == "group":
            promo.allowed_group = None if value.lower() in {"none", "-"} else value[:32]
        elif key == "new":
            promo.new_users_only = _bool(value)
        elif key == "value":
            promo.value = _int(value, "value", 1)
        elif key == "desc":
            promo.description = value.replace("_", " ")[:300]
        elif key == "active":
            promo.is_active = _bool(value)
        else:
            raise AdminInputError(f"Неизвестное поле: {key}")
    if promo.kind == PromoKind.PERCENT and not 1 <= promo.value <= 100:
        raise AdminInputError("Процент скидки: 1–100")
    if promo.kind == PromoKind.DAYS and promo.value > 3650:
        raise AdminInputError("Не больше 3650 дней")


async def create_promo(
    session: AsyncSession, settings: Settings, admin_id: int, code: str, kind: str, value: int, args: list[str]
) -> PromoCode:
    require_admin(settings, admin_id)
    norm = normalize_code(code)
    if norm is None:
        raise AdminInputError("Код: A-Z, 0-9, «-», «_», 3–32 символа")
    if kind not in PromoKind.ALL:
        raise AdminInputError(f"Тип: {', '.join(PromoKind.ALL)}")
    if await session.scalar(select(PromoCode.id).where(PromoCode.code == norm)):
        raise AdminInputError("Такой промокод уже существует")
    promo = PromoCode(code=norm, kind=kind, value=_int(str(value), "value", 1), description="")
    _apply_promo_fields(promo, _parse_kv(args))
    session.add(promo)
    await audit(session, admin_id, "promo_create", norm, kind=kind, value=value, args=args)
    await session.commit()
    return promo


async def update_promo(session: AsyncSession, settings: Settings, admin_id: int, code: str, args: list[str]) -> PromoCode:
    require_admin(settings, admin_id)
    promo = await session.scalar(select(PromoCode).where(PromoCode.code == (normalize_code(code) or "")))
    if promo is None:
        raise AdminInputError("Промокод не найден")
    fields = _parse_kv(args)
    _apply_promo_fields(promo, fields)
    await audit(session, admin_id, "promo_update", promo.code, **fields)
    await session.commit()
    return promo


async def archive_promo(session: AsyncSession, settings: Settings, admin_id: int, code: str) -> PromoCode:
    require_admin(settings, admin_id)
    promo = await session.scalar(select(PromoCode).where(PromoCode.code == (normalize_code(code) or "")))
    if promo is None:
        raise AdminInputError("Промокод не найден")
    promo.is_archived, promo.is_active = True, False
    await audit(session, admin_id, "promo_archive", promo.code)
    await session.commit()
    return promo


async def promo_stats(session: AsyncSession, settings: Settings, admin_id: int, code: str) -> tuple[PromoCode, int, int]:
    require_admin(settings, admin_id)
    promo = await session.scalar(select(PromoCode).where(PromoCode.code == (normalize_code(code) or "")))
    if promo is None:
        raise AdminInputError("Промокод не найден")
    used = await session.scalar(
        select(func.count(PromoActivation.id)).where(PromoActivation.promo_id == promo.id, PromoActivation.status == "used")
    ) or 0
    paid = await session.scalar(
        select(func.count(Order.id))
        .join(PromoActivation, PromoActivation.id == Order.promo_activation_id)
        .where(PromoActivation.promo_id == promo.id, Order.status == OrderStatus.PAID)
    ) or 0
    return promo, used, paid


# ---------- settings ----------

async def change_setting(session: AsyncSession, settings: Settings, admin_id: int, key: str, value: str) -> str:
    require_admin(settings, admin_id)
    try:
        stored = await settings_store.set_setting(session, key, value)
    except settings_store.SettingError as exc:
        raise AdminInputError(str(exc)) from exc
    await audit(session, admin_id, "setting", key, value=stored[:200])
    await session.commit()
    return stored


async def recent_audit(session: AsyncSession, settings: Settings, admin_id: int, limit: int = 20) -> list[AuditLog]:
    require_admin(settings, admin_id)
    return list((await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))).all())

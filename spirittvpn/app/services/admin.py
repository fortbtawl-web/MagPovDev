"""Admin operations. Every function checks the role permission server-side and writes the audit log."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import (
    AuditLog,
    BonusEntry,
    BonusSource,
    Device,
    DeviceStatus,
    Order,
    OrderStatus,
    Plan,
    PromoActivation,
    PromoCode,
    PromoKind,
    Staff,
    Subscription,
    SubStatus,
    TrialActivation,
    User,
    VPNErrorLog,
    VPNNode,
)
from app.services import referrals, settings_store
from app.services.devices import revoke_device_row
from app.services.orders import refund_order, user_orders
from app.services.promos import normalize_code, user_activations
from app.services.roles import ROLE_PERMS, AdminPermissionError, require
from app.services.users import extend_subscription, get_subscription, get_user, is_active, live_devices
from app.vpn.base import VPNService

log = logging.getLogger("spirittvpn.admin")

__all__ = ["AdminPermissionError", "AdminInputError"]


class AdminInputError(ValueError):
    pass


async def audit(session: AsyncSession, admin_id: int, action: str, target: str | None = None, **details) -> None:
    session.add(AuditLog(
        admin_telegram_id=admin_id, action=action, target=target,
        details=json.dumps(details, ensure_ascii=False, default=str) if details else None,
    ))


def _reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise AdminInputError("Укажи причину")
    return reason[:200]


# ---------- dashboard ----------

@dataclass(frozen=True)
class Stats:
    users_total: int
    users_day: int
    users_month: int
    active_subs: int
    expired_subs: int
    active_trials: int
    trials_used: int
    paid_orders: int
    revenue: dict[str, dict[str, int]]  # period -> currency -> amount
    new_purchases_30d: int
    renewals_30d: int
    active_promos: int
    devices_live: int
    referrals: dict
    nodes_total: int
    nodes_online: int
    vpn_errors_24h: int


async def stats(session: AsyncSession, settings: Settings, admin_id: int, now: datetime) -> Stats:
    await require(session, settings, admin_id, "stats.view")
    c = session.scalar
    active_cond = (Subscription.status == SubStatus.ACTIVE) & ((Subscription.expires_at > now) | Subscription.is_lifetime)
    revenue: dict[str, dict[str, int]] = {}
    for period, days in (("day", 1), ("week", 7), ("month", 30)):
        rows = await session.execute(
            select(Order.currency, func.sum(Order.amount))
            .where(Order.status == OrderStatus.PAID, Order.is_test.is_(False), Order.paid_at >= now - timedelta(days=days))
            .group_by(Order.currency)
        )
        revenue[period] = {cur: int(total or 0) for cur, total in rows.all()}
    paid_30 = (await session.execute(
        select(Order.user_id, Order.paid_at).where(
            Order.status == OrderStatus.PAID, Order.is_test.is_(False), Order.paid_at >= now - timedelta(days=30))
    )).all()
    renewals = 0
    for user_id, paid_at in paid_30:
        earlier = await c(select(Order.id).where(Order.user_id == user_id, Order.status == OrderStatus.PAID,
                                                 Order.paid_at < paid_at).limit(1))
        renewals += earlier is not None
    return Stats(
        users_total=await c(select(func.count(User.id))) or 0,
        users_day=await c(select(func.count(User.id)).where(User.created_at >= now - timedelta(days=1))) or 0,
        users_month=await c(select(func.count(User.id)).where(User.created_at >= now - timedelta(days=30))) or 0,
        active_subs=await c(select(func.count(Subscription.id)).where(active_cond)) or 0,
        expired_subs=await c(select(func.count(Subscription.id)).where(Subscription.status == SubStatus.EXPIRED)) or 0,
        active_trials=await c(select(func.count(Subscription.id)).where(active_cond, Subscription.is_trial)) or 0,
        trials_used=await c(select(func.count(TrialActivation.id))) or 0,
        paid_orders=await c(select(func.count(Order.id)).where(Order.status == OrderStatus.PAID, Order.is_test.is_(False))) or 0,
        revenue=revenue,
        new_purchases_30d=len(paid_30) - renewals,
        renewals_30d=renewals,
        active_promos=await c(select(func.count(PromoCode.id)).where(
            PromoCode.is_active, PromoCode.is_archived.is_(False),
            (PromoCode.valid_until.is_(None)) | (PromoCode.valid_until > now))) or 0,
        devices_live=await c(select(func.count(Device.id)).where(Device.status.in_(DeviceStatus.LIVE))) or 0,
        referrals=await referrals.program_stats(session),
        nodes_total=await c(select(func.count(VPNNode.id))) or 0,
        nodes_online=await c(select(func.count(VPNNode.id)).where(VPNNode.is_connected, VPNNode.is_disabled.is_(False))) or 0,
        vpn_errors_24h=await c(select(func.count(VPNErrorLog.id)).where(VPNErrorLog.created_at >= now - timedelta(days=1))) or 0,
    )


# ---------- users ----------

@dataclass
class UserCard:
    user: User
    subscription: Subscription | None
    orders: list[Order]
    activations: list[tuple[PromoActivation, PromoCode]]
    trial_used: bool
    devices: list[Device]
    bonuses: list[BonusEntry]
    referral: referrals.ReferralSummary


async def user_card(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int) -> UserCard | None:
    await require(session, settings, admin_id, "users.view")
    user = await get_user(session, telegram_id)
    if user is None:
        return None
    trial = await session.scalar(select(TrialActivation.id).where(TrialActivation.user_id == user.id))
    return UserCard(
        user, await get_subscription(session, user.id), await user_orders(session, user.id, 20),
        await user_activations(session, user.id), trial is not None, await live_devices(session, user.id),
        await referrals.bonus_history(session, user.id, 20), await referrals.summary(session, user),
    )


async def _target(session: AsyncSession, telegram_id: int) -> User:
    user = await get_user(session, telegram_id)
    if user is None:
        raise AdminInputError("Пользователь не найден")
    return user


async def manual_extend(
    session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, days: int, reason: str, now: datetime,
    *, test_access: bool = False,
) -> Subscription:
    await require(session, settings, admin_id, "users.manage")
    if not 1 <= days <= 3650:
        raise AdminInputError("Количество дней: 1–3650")
    reason = _reason(reason)
    user = await _target(session, telegram_id)
    current = await get_subscription(session, user.id)
    kwargs = {}
    if test_access and not is_active(current, now):
        kwargs = dict(plan_code="test_access", plan_title="Тестовый доступ", device_limit=1)
    sub = await extend_subscription(session, user, days, now, **kwargs)
    session.add(BonusEntry(user_id=user.id, source=BonusSource.ADMIN, days=days, created_by=admin_id, created_at=now,
                           description=("Тестовый доступ: " if test_access else "Продление администратором: ") + reason))
    await audit(session, admin_id, "test_access" if test_access else "manual_extend", str(telegram_id), days=days,
                reason=reason, new_expires_at=sub.expires_at.isoformat())
    await session.commit()
    return sub


async def set_blocked(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, blocked: bool,
                      now: datetime | None = None, reason: str = "") -> User:
    """Blocking also suspends the subscription (VPN disabled); unblocking restores it."""
    await require(session, settings, admin_id, "users.manage")
    if settings.env_role(telegram_id) or await session.get(Staff, telegram_id):
        raise AdminInputError("Нельзя заблокировать сотрудника")
    now = now or datetime.now(UTC)
    user = await _target(session, telegram_id)
    user.is_blocked = blocked
    sub = await get_subscription(session, user.id, for_update=True)
    if sub is not None:
        if blocked and sub.status == SubStatus.ACTIVE:
            sub.status = SubStatus.SUSPENDED
            sub.vpn_sync_needed = True
        elif not blocked and sub.status == SubStatus.SUSPENDED:
            sub.status = SubStatus.ACTIVE if (sub.is_lifetime or sub.expires_at > now) else SubStatus.EXPIRED
            sub.vpn_sync_needed = True
    await audit(session, admin_id, "block" if blocked else "unblock", str(telegram_id), reason=reason[:200])
    await session.commit()
    return user


async def set_user_group(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, group: str | None) -> User:
    await require(session, settings, admin_id, "users.manage")
    user = await _target(session, telegram_id)
    user.group = (group or "").strip()[:32] or None
    await audit(session, admin_id, "set_group", str(telegram_id), group=user.group)
    await session.commit()
    return user


async def admin_revoke_device(
    session: AsyncSession, settings: Settings, vpn: VPNService, admin_id: int, public_id: str, reason: str, now: datetime
) -> Device:
    await require(session, settings, admin_id, "devices.manage")
    reason = _reason(reason)
    device = await session.scalar(select(Device).where(Device.public_id == public_id, Device.status.in_(DeviceStatus.LIVE)))
    if device is None:
        raise AdminInputError("Устройство не найдено")
    await audit(session, admin_id, "device_revoke", public_id, reason=reason)
    await session.commit()
    err = await revoke_device_row(session, vpn, device, now)
    if err:
        raise AdminInputError(err.value)
    return device


async def admin_refund(session: AsyncSession, settings: Settings, admin_id: int, order_id: str, reason: str, now: datetime) -> Order:
    """Records a refund made at the provider (the provider call itself is done by the caller)."""
    await require(session, settings, admin_id, "payments.manage")
    reason = _reason(reason)
    order = await refund_order(session, order_id, now)
    if order is None:
        raise AdminInputError("Можно вернуть только оплаченный заказ")
    await audit(session, admin_id, "refund", order_id, reason=reason, amount=order.amount, currency=order.currency)
    await session.commit()
    return order


# ---------- parsing helpers (bot commands) ----------

def _parse_kv(args: list[str]) -> dict[str, str]:
    out = {}
    for a in args:
        if "=" not in a:
            raise AdminInputError(f"Ожидается ключ=значение, получено: {a}")
        k, v = a.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


def _int(v: object, name: str, lo: int = 0, hi: int = 10**9) -> int:
    try:
        n = int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise AdminInputError(f"{name}: нужно целое число") from None
    if not lo <= n <= hi:
        raise AdminInputError(f"{name}: допустимо {lo}–{hi}")
    return n


def _bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if str(v).lower() in {"1", "on", "true", "да"}:
        return True
    if str(v).lower() in {"0", "off", "false", "нет"}:
        return False
    raise AdminInputError("Ожидается on/off")


def _date(v: object) -> datetime | None:
    v = "" if v is None else str(v)
    if v.lower() in {"", "none", "-"}:
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        raise AdminInputError("Дата в формате ГГГГ-ММ-ДД") from None


# ---------- plans ----------

async def list_plans(session: AsyncSession, settings: Settings, admin_id: int) -> list[Plan]:
    await require(session, settings, admin_id, "plans.manage")
    return list((await session.scalars(select(Plan).order_by(Plan.sort_order, Plan.id))).all())


async def create_plan(session: AsyncSession, settings: Settings, admin_id: int, code: str, days: int, price: int,
                      title: str, device_limit: int = 1, lifetime: bool = False) -> Plan:
    await require(session, settings, admin_id, "plans.manage")
    code = code.strip().lower()
    if not code.isascii() or not code.replace("_", "").isalnum() or len(code) > 32:
        raise AdminInputError("Код тарифа: латиница/цифры, до 32 символов")
    if await session.scalar(select(Plan.id).where(Plan.code == code)):
        raise AdminInputError("Тариф с таким кодом уже есть")
    plan = Plan(
        code=code, title=title.strip()[:128] or code, description="",
        duration_days=0 if lifetime else _int(days, "days", 1, 3650), device_limit=_int(device_limit, "devices", 1, 20),
        is_lifetime=lifetime, price=_int(price, "price", 1), currency=settings.payment_currency, is_active=False,
        sort_order=100,
    )
    session.add(plan)
    await audit(session, admin_id, "plan_create", code, days=days, price=price, devices=device_limit, lifetime=lifetime)
    await session.commit()
    return plan


async def update_plan(session: AsyncSession, settings: Settings, admin_id: int, code: str, changes: dict | list[str]) -> Plan:
    """Changes apply to new orders only: paid orders keep their snapshot (price, days, devices)."""
    await require(session, settings, admin_id, "plans.manage")
    plan = await session.scalar(select(Plan).where(Plan.code == code.strip().lower()))
    if plan is None:
        raise AdminInputError("Тариф не найден")
    fields = _parse_kv(changes) if isinstance(changes, list) else dict(changes)
    for key, value in fields.items():
        if key == "price":
            plan.price = _int(value, "price", 1)
        elif key == "days":
            if not plan.is_lifetime:
                plan.duration_days = _int(value, "days", 1, 3650)
        elif key in ("devices", "device_limit"):
            plan.device_limit = _int(value, "devices", 1, 20)
        elif key == "title":
            plan.title = str(value).replace("_", " ")[:128]
        elif key in ("desc", "description"):
            plan.description = str(value).replace("_", " ")[:500] if isinstance(changes, list) else str(value)[:500]
        elif key in ("active", "is_active"):
            plan.is_active = _bool(value)
        elif key in ("sort", "sort_order"):
            plan.sort_order = _int(value, "sort")
        else:
            raise AdminInputError(f"Неизвестное поле: {key}")
    if plan.is_active and plan.price <= 0:
        raise AdminInputError("Нельзя включить тариф без цены")
    await audit(session, admin_id, "plan_update", plan.code, **{k: str(v) for k, v in fields.items()})
    await session.commit()
    return plan


# ---------- promo codes ----------

async def list_promos(session: AsyncSession, settings: Settings, admin_id: int, include_archived: bool = False) -> list[PromoCode]:
    await require(session, settings, admin_id, "promos.manage")
    stmt = select(PromoCode).order_by(PromoCode.created_at.desc()).limit(100)
    if not include_archived:
        stmt = stmt.where(PromoCode.is_archived.is_(False))
    return list((await session.scalars(stmt)).all())


def _apply_promo_fields(promo: PromoCode, fields: dict, underscore_spaces: bool = True) -> None:
    for key, value in fields.items():
        if key in ("max", "max_activations"):
            promo.max_activations = None if str(value).lower() in {"none", "-", "0", ""} else _int(value, "max", 1)
        elif key in ("until", "valid_until"):
            promo.valid_until = _date(value)
        elif key in ("from", "valid_from"):
            d = _date(value)
            promo.valid_from = d - timedelta(days=1) + timedelta(seconds=1) if d else None
        elif key in ("group", "allowed_group"):
            promo.allowed_group = None if str(value).lower() in {"none", "-", ""} else str(value)[:32]
        elif key in ("plans", "allowed_plans"):
            codes = [c.strip().lower() for c in str(value).split(",") if c.strip() and c.strip() not in {"-", "none"}]
            promo.allowed_plans = ",".join(codes)[:255] or None
        elif key in ("new", "new_users_only"):
            promo.new_users_only = _bool(value)
        elif key == "value":
            promo.value = _int(value, "value", 1)
        elif key in ("desc", "description"):
            promo.description = (str(value).replace("_", " ") if underscore_spaces else str(value))[:300]
        elif key in ("active", "is_active"):
            promo.is_active = _bool(value)
        else:
            raise AdminInputError(f"Неизвестное поле: {key}")
    if promo.kind == PromoKind.PERCENT and not 1 <= promo.value <= 100:
        raise AdminInputError("Процент скидки: 1–100")
    if promo.kind == PromoKind.DAYS and promo.value > 3650:
        raise AdminInputError("Не больше 3650 дней")


async def create_promo(session: AsyncSession, settings: Settings, admin_id: int, code: str, kind: str, value: int,
                       args: list[str] | dict) -> PromoCode:
    await require(session, settings, admin_id, "promos.manage")
    norm = normalize_code(code)
    if norm is None:
        raise AdminInputError("Код: A-Z, 0-9, «-», «_», 3–32 символа")
    if kind not in PromoKind.ALL:
        raise AdminInputError(f"Тип: {', '.join(PromoKind.ALL)}")
    if await session.scalar(select(PromoCode.id).where(PromoCode.code == norm)):
        raise AdminInputError("Такой промокод уже существует")
    promo = PromoCode(code=norm, kind=kind, value=_int(value, "value", 1), description="", activations_count=0,
                      is_active=True, is_archived=False, new_users_only=False)
    is_list = isinstance(args, list)
    _apply_promo_fields(promo, _parse_kv(args) if is_list else dict(args), underscore_spaces=is_list)
    session.add(promo)
    await audit(session, admin_id, "promo_create", norm, kind=kind, value=value, args=args)
    await session.commit()
    return promo


async def _promo(session: AsyncSession, code: str) -> PromoCode:
    promo = await session.scalar(select(PromoCode).where(PromoCode.code == (normalize_code(code) or "")))
    if promo is None:
        raise AdminInputError("Промокод не найден")
    return promo


async def update_promo(session: AsyncSession, settings: Settings, admin_id: int, code: str, args: list[str] | dict) -> PromoCode:
    await require(session, settings, admin_id, "promos.manage")
    promo = await _promo(session, code)
    is_list = isinstance(args, list)
    fields = _parse_kv(args) if is_list else dict(args)
    _apply_promo_fields(promo, fields, underscore_spaces=is_list)
    await audit(session, admin_id, "promo_update", promo.code, **{k: str(v) for k, v in fields.items()})
    await session.commit()
    return promo


async def archive_promo(session: AsyncSession, settings: Settings, admin_id: int, code: str) -> PromoCode:
    await require(session, settings, admin_id, "promos.manage")
    promo = await _promo(session, code)
    promo.is_archived, promo.is_active = True, False
    await audit(session, admin_id, "promo_archive", promo.code)
    await session.commit()
    return promo


async def promo_stats(session: AsyncSession, settings: Settings, admin_id: int, code: str) -> tuple[PromoCode, int, int]:
    await require(session, settings, admin_id, "promos.manage")
    promo = await _promo(session, code)
    used = await session.scalar(
        select(func.count(PromoActivation.id)).where(PromoActivation.promo_id == promo.id, PromoActivation.status == "used")
    ) or 0
    paid = await session.scalar(
        select(func.count(Order.id)).join(PromoActivation, PromoActivation.id == Order.promo_activation_id)
        .where(PromoActivation.promo_id == promo.id, Order.status == OrderStatus.PAID)
    ) or 0
    return promo, used, paid


# ---------- referrals ----------

async def referral_overview(session: AsyncSession, settings: Settings, admin_id: int) -> tuple[dict, list[BonusEntry]]:
    await require(session, settings, admin_id, "referrals.manage")
    history = (await session.scalars(
        select(BonusEntry).where(BonusEntry.source.in_((*BonusSource.REFERRAL, BonusSource.CORRECTION)))
        .order_by(BonusEntry.id.desc()).limit(50)
    )).all()
    return await referrals.program_stats(session), list(history)


async def update_referral_settings(session: AsyncSession, settings: Settings, admin_id: int, changes: dict) -> dict:
    await require(session, settings, admin_id, "referrals.manage")
    mapping = {"enabled": "referral_enabled", "trial_days": "referral_trial_days", "payment_days": "referral_payment_days"}
    for key, value in changes.items():
        if key not in mapping:
            raise AdminInputError(f"Неизвестное поле: {key}")
        raw = ("on" if value else "off") if isinstance(value, bool) else str(value)
        try:
            await settings_store.set_setting(session, mapping[key], raw)
        except settings_store.SettingError as exc:
            raise AdminInputError(str(exc)) from exc
    await audit(session, admin_id, "referral_settings", None, **{k: str(v) for k, v in changes.items()})
    await session.commit()
    return await referrals.program_stats(session)


async def referral_correction(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, days: int,
                              reason: str, now: datetime) -> BonusEntry:
    await require(session, settings, admin_id, "referrals.manage")
    reason = _reason(reason)
    days = _int(days, "days", -3650, 3650)
    if days == 0:
        raise AdminInputError("Количество дней не может быть 0")
    user = await _target(session, telegram_id)
    try:
        entry = await referrals.apply_correction(session, user, days, reason, admin_id, now)
    except ValueError as exc:
        raise AdminInputError(str(exc)) from exc
    await audit(session, admin_id, "bonus_correction", str(telegram_id), days=days, reason=reason)
    await session.commit()
    return entry


# ---------- staff ----------

async def list_staff(session: AsyncSession, settings: Settings, admin_id: int) -> list[tuple[int, str, str]]:
    await require(session, settings, admin_id, "staff.manage")
    out = [(tg, "owner", "env") for tg in sorted(settings.owner_ids)]
    out += [(tg, "admin", "env") for tg in sorted(settings.admin_ids - settings.owner_ids)]
    out += [(s.telegram_id, s.role, "panel") for s in (await session.scalars(select(Staff).order_by(Staff.created_at))).all()]
    return out


async def set_staff(session: AsyncSession, settings: Settings, admin_id: int, telegram_id: int, role: str | None) -> None:
    await require(session, settings, admin_id, "staff.manage")
    if settings.env_role(telegram_id):
        raise AdminInputError("Роль задана в переменных окружения — меняется только там")
    if role is not None and (role not in ROLE_PERMS or role == "owner"):
        raise AdminInputError("Роль: admin, support или tech")
    row = await session.get(Staff, telegram_id)
    if role is None:
        if row:
            await session.delete(row)
    elif row:
        row.role = role
    else:
        session.add(Staff(telegram_id=telegram_id, role=role, added_by=admin_id))
    await audit(session, admin_id, "staff_set", str(telegram_id), role=role)
    await session.commit()


# ---------- settings / audit ----------

async def change_setting(session: AsyncSession, settings: Settings, admin_id: int, key: str, value: str) -> str:
    await require(session, settings, admin_id, "settings.manage")
    try:
        stored = await settings_store.set_setting(session, key, value)
    except settings_store.SettingError as exc:
        raise AdminInputError(str(exc)) from exc
    await audit(session, admin_id, "setting", key, value=stored[:200])
    await session.commit()
    return stored


async def recent_audit(session: AsyncSession, settings: Settings, admin_id: int, limit: int = 30) -> list[AuditLog]:
    await require(session, settings, admin_id, "audit.view")
    return list((await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))).all())

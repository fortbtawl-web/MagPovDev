"""Promo codes: percent / fixed discounts on the next order, or free subscription days.

All checks run on the server inside one transaction with the promo row locked, so the activation
limit can't be exceeded by concurrent requests; a DB unique constraint forbids reuse by the same user.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.models import Device, DevStatus, PromoActivation, PromoCode, Subscription, SubStatus, User
from backend.services import admin as admin_svc
from backend.services import devices as dev_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_money, utcnow

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
KIND_LABELS = {"percent": "Скидка %", "fixed": "Скидка ₽", "days": "Бесплатные дни"}
MIN_ORDER_MINOR = 100  # YooKassa minimum: 1 ₽


def normalize(code: str | None) -> str:
    return (code or "").strip().upper().replace(" ", "")


def value_label(kind: str, value: int) -> str:
    if kind == "percent":
        return f"−{value}%"
    if kind == "fixed":
        return f"−{fmt_money(value)}"
    return f"+{value} дн."


def discount_for(price_minor: int, promo: PromoCode) -> int:
    if promo.kind == "percent":
        discount = price_minor * promo.value // 100
    elif promo.kind == "fixed":
        discount = promo.value
    else:
        return 0
    return max(0, min(discount, price_minor - MIN_ORDER_MINOR))


def _usable(promo: PromoCode, now: datetime) -> str | None:
    """None if usable, otherwise the reason shown to the user."""
    if not promo.is_active:
        return "Промокод отключён."
    if promo.starts_at and promo.starts_at > now:
        return "Промокод ещё не действует."
    if promo.expires_at and promo.expires_at <= now:
        return "Срок действия промокода истёк."
    return None


@dataclass
class ActivationResult:
    kind: str
    message: str
    days: int | None = None
    discount_label: str | None = None
    subscription: Subscription | None = None


async def activate(ctx: AppContext, user_id: int, raw_code: str) -> ActivationResult:
    code = normalize(raw_code)
    if not CODE_RE.match(code):
        raise ServiceError("Промокод не найден. Проверьте написание.", "promo_not_found")
    now = utcnow()
    created_sub = False
    try:
        async with ctx.session() as session, session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                raise ServiceError("Нажмите /start.", "not_found")
            promo = await session.scalar(select(PromoCode).where(PromoCode.code == code).with_for_update())
            if promo is None:
                raise ServiceError("Промокод не найден. Проверьте написание.", "promo_not_found")
            reason = _usable(promo, now)
            if reason:
                raise ServiceError(reason, "promo_unavailable")
            if await session.scalar(select(PromoActivation.id).where(PromoActivation.promo_id == promo.id, PromoActivation.user_id == user_id)):
                raise ServiceError("Вы уже использовали этот промокод.", "promo_used")
            if promo.max_activations is not None and promo.activations >= promo.max_activations:
                raise ServiceError("Лимит активаций промокода исчерпан.", "promo_exhausted")

            activation = PromoActivation(promo_id=promo.id, user_id=user_id, status="reserved")
            result = ActivationResult(kind=promo.kind, message="")
            if promo.kind in ("percent", "fixed"):
                pending = await active_discount(session, user_id)
                if pending is not None:
                    raise ServiceError(f"У вас уже есть неиспользованная скидка по промокоду {pending[1].code}. Сначала оформите заказ.", "promo_has_discount")
                result.discount_label = value_label(promo.kind, promo.value)
                result.message = f"✅ Промокод {promo.code} активирован: скидка {result.discount_label} применится к следующему заказу."
            else:
                sub = await subs_svc.get_current(session, user_id, lock=True)
                if sub is not None and sub.is_lifetime:
                    raise ServiceError("У вас бессрочная подписка — бесплатные дни не нужны.", "lifetime")
                if sub is not None and sub.status in (SubStatus.PENDING, SubStatus.SUSPENDED):
                    raise ServiceError("Сейчас нельзя добавить дни: подписка оформляется или приостановлена.", "bad_state")
                if sub is None:
                    sub = Subscription(user_id=user_id, plan_code="promo", status=SubStatus.ACTIVE, device_limit=1, source="promo", starts_at=now, expires_at=now + timedelta(days=promo.value))
                    session.add(sub)
                    await session.flush()
                    subs_svc.log_event(session, sub, "promo_days", actor="user", days=promo.value, comment=f"promo {promo.code}").public_note = f"Промокод {promo.code}"
                    created_sub = True
                else:
                    await subs_svc.add_days(session, sub, promo.value, action="promo_days", actor="user", comment=f"promo {promo.code}")
                    await session.flush()
                activation.status, activation.used_at, activation.subscription_id = "applied", now, sub.id
                result.days = promo.value
                result.subscription = sub
                result.message = f"✅ Промокод {promo.code} активирован: +{promo.value} дн. подписки."
            session.add(activation)
            promo.activations += 1
            await session.flush()
    except IntegrityError as exc:
        # Lost a race against a parallel activation by the same user.
        raise ServiceError("Вы уже использовали этот промокод.", "promo_used") from exc

    if result.subscription is not None:
        sub_id = result.subscription.id
        async with ctx.session() as session:
            auto = bool(await settings_svc.get(session, "auto_provision_first_device"))
            has_devices = await session.scalar(select(Device.id).where(Device.subscription_id == sub_id, Device.status.in_(DevStatus.OCCUPYING)).limit(1))
        if created_sub and auto and not has_devices:
            try:
                res = await dev_svc.add_device(ctx, user_id, label="Устройство 1", actor="user")
                if not res.confirmed:
                    result.message += "\n⏳ VPN-сервер ещё не подтвердил подключение — повторим автоматически."
            except ServiceError as exc:
                result.message += f"\n⚠️ Подключение пока не создано: {exc.message}"
        else:
            await dev_svc.sync_subscription(ctx, sub_id, actor="user")
        async with ctx.session() as session:
            result.subscription = await session.get(Subscription, sub_id)
    return result


async def active_discount(session, user_id: int) -> tuple[PromoActivation, PromoCode] | None:
    """The user's reserved, still valid discount (promo active and not expired)."""
    row = (
        await session.execute(
            select(PromoActivation, PromoCode)
            .join(PromoCode, PromoCode.id == PromoActivation.promo_id)
            .where(PromoActivation.user_id == user_id, PromoActivation.status == "reserved", PromoCode.kind.in_(("percent", "fixed")))
            .order_by(PromoActivation.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None or _usable(row[1], utcnow()) is not None:
        return None
    return row[0], row[1]


# ---------------- admin ----------------

def _promo_dict(p: PromoCode) -> dict:
    return {"code": p.code, "kind": p.kind, "value": p.value, "max_activations": p.max_activations, "starts_at": p.starts_at, "expires_at": p.expires_at, "is_active": p.is_active, "note": p.note}


async def create(
    ctx: AppContext,
    admin_id: int,
    source: str,
    *,
    code: str | None,
    kind: str,
    value: int,
    max_activations: int | None = None,
    expires_at: datetime | None = None,
    note: str | None = None,
    reason: str,
) -> PromoCode:
    reason = admin_svc.require_reason(reason)
    if kind not in KIND_LABELS:
        raise ServiceError("Тип промокода: percent, fixed или days.", "bad_kind")
    bounds = {"percent": (1, 90), "fixed": (100, 10_000_000), "days": (1, 365)}[kind]
    if not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
        msg = {"percent": "Скидка: от 1 до 90%.", "fixed": "Скидка: от 1 до 100 000 ₽.", "days": "Дней: от 1 до 365."}[kind]
        raise ServiceError(msg, "bad_value")
    if max_activations is not None and not 1 <= max_activations <= 1_000_000:
        raise ServiceError("Лимит активаций: от 1.", "bad_limit")
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utcnow():
            raise ServiceError("Срок действия должен быть в будущем.", "bad_date")
    code = normalize(code) or "SV" + "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(8))
    if not CODE_RE.match(code):
        raise ServiceError("Код: латиница, цифры, «_» или «-», от 3 до 32 символов.", "bad_code")
    async with ctx.session() as session, session.begin():
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == code)):
            raise ServiceError("Такой промокод уже существует.", "duplicate")
        promo = PromoCode(code=code, kind=kind, value=value, max_activations=max_activations, expires_at=expires_at, note=(note or "").strip()[:200] or None, created_by=admin_id)
        session.add(promo)
        await session.flush()
        admin_svc.audit(session, admin_id=admin_id, source=source, action="promo.create", entity_type="promo", entity_id=promo.id, reason=reason, new=_promo_dict(promo))
    return promo


async def set_active(ctx: AppContext, admin_id: int, source: str, promo_id: int, active: bool, reason: str) -> PromoCode:
    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        promo = await session.get(PromoCode, promo_id, with_for_update=True)
        if promo is None:
            raise ServiceError("Промокод не найден.", "not_found")
        old = promo.is_active
        promo.is_active = active
        admin_svc.audit(session, admin_id=admin_id, source=source, action="promo.enable" if active else "promo.disable", entity_type="promo", entity_id=promo.id, reason=reason, old={"is_active": old}, new={"is_active": active})
    return promo


async def list_promos(ctx: AppContext, limit: int = 200) -> list[PromoCode]:
    async with ctx.session() as session:
        return list((await session.execute(select(PromoCode).order_by(PromoCode.created_at.desc()).limit(limit))).scalars())


async def list_activations(ctx: AppContext, promo_id: int, limit: int = 500) -> list[tuple[PromoActivation, User]]:
    async with ctx.session() as session:
        rows = await session.execute(
            select(PromoActivation, User).join(User, User.id == PromoActivation.user_id).where(PromoActivation.promo_id == promo_id).order_by(PromoActivation.created_at.desc()).limit(limit)
        )
        return [(a, u) for a, u in rows.all()]


def promo_json(p: PromoCode, stats: dict | None = None) -> dict:
    from backend.api.serializers import ts

    return {
        "starts_at": ts(p.starts_at), "per_user_limit": 1, "stats": stats,
        "id": p.id, "code": p.code, "kind": p.kind, "kind_label": KIND_LABELS[p.kind], "value": p.value, "value_label": value_label(p.kind, p.value),
        "max_activations": p.max_activations, "activations": p.activations, "expires_at": ts(p.expires_at), "is_active": p.is_active,
        "note": p.note, "created_at": ts(p.created_at), "created_by": p.created_by,
    }


async def promo_stats(session, promo: PromoCode) -> dict:
    """Usage vs. sales. Derived from immutable order snapshots, so webhook retries can't inflate it."""
    from backend.api.serializers import ts
    from backend.models import Order, OrderStatus
    from backend.utils import fmt_money

    acts, users, first, last = (await session.execute(
        select(func.count(PromoActivation.id), func.count(func.distinct(PromoActivation.user_id)),
               func.min(PromoActivation.created_at), func.max(PromoActivation.created_at)).where(PromoActivation.promo_id == promo.id)
    )).one()
    rows = dict((await session.execute(
        select(Order.status, func.count(Order.id)).where(Order.promo_code == promo.code).group_by(Order.status)
    )).all())
    paid_sum, discount_sum = (await session.execute(
        select(func.coalesce(func.sum(Order.amount_minor), 0), func.coalesce(func.sum(Order.discount_minor), 0))
        .where(Order.promo_code == promo.code, Order.status == OrderStatus.PAID)
    )).one()
    return {
        "activations": int(acts or 0), "unique_users": int(users or 0), "orders": int(sum(rows.values())),
        "paid_orders": int(rows.get(OrderStatus.PAID, 0)), "canceled_orders": int(rows.get(OrderStatus.CANCELED, 0) + rows.get(OrderStatus.FAILED, 0)),
        "refunded_orders": int(rows.get(OrderStatus.REFUNDED, 0)), "paid_minor": int(paid_sum), "paid_label": fmt_money(int(paid_sum)),
        "discount_minor": int(discount_sum), "discount_label": fmt_money(int(discount_sum)),
        "first_used_at": ts(first), "last_used_at": ts(last),
    }


async def update(ctx: AppContext, admin_id: int, source: str, promo_id: int, changes: dict, reason: str) -> PromoCode:
    """Edit value/limits/dates. The kind is fixed; existing orders keep their snapshot discount."""
    reason = admin_svc.require_reason(reason)
    allowed = {"value", "max_activations", "starts_at", "expires_at", "note"}
    changes = {k: v for k, v in changes.items() if k in allowed}
    if not changes:
        raise ServiceError("Нет изменений.", "no_changes")
    async with ctx.session() as session, session.begin():
        promo = await session.get(PromoCode, promo_id, with_for_update=True)
        if promo is None:
            raise ServiceError("Промокод не найден.", "not_found")
        old = _promo_dict(promo)
        if "value" in changes:
            lo, hi = {"percent": (1, 90), "fixed": (100, 10_000_000), "days": (1, 365)}[promo.kind]
            if not isinstance(changes["value"], int) or not lo <= changes["value"] <= hi:
                raise ServiceError({"percent": "Скидка: от 1 до 90%.", "fixed": "Скидка: от 1 до 100 000 ₽.", "days": "Дней: от 1 до 365."}[promo.kind], "bad_value")
            promo.value = changes["value"]
        if "max_activations" in changes:
            m = changes["max_activations"]
            if m is not None and (not isinstance(m, int) or m < max(1, promo.activations)):
                raise ServiceError(f"Лимит не может быть меньше уже использованных ({promo.activations}).", "bad_limit")
            promo.max_activations = m
        for key in ("starts_at", "expires_at"):
            if key in changes:
                v = changes[key]
                if v is not None and v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                setattr(promo, key, v)
        if promo.starts_at and promo.expires_at and promo.expires_at <= promo.starts_at:
            raise ServiceError("Дата окончания должна быть позже даты начала.", "bad_date")
        if "note" in changes:
            promo.note = (changes["note"] or "").strip()[:200] or None
        await session.flush()
        admin_svc.audit(session, admin_id=admin_id, source=source, action="promo.update", entity_type="promo", entity_id=promo.id, reason=reason, old=old, new=_promo_dict(promo))
    return promo


async def promo_orders(ctx: AppContext, promo_id: int, limit: int = 300) -> list[dict]:
    from backend.api.serializers import ts
    from backend.models import Order, Payment
    from backend.services.payments import ORDER_STATUS_LABELS
    from backend.utils import fmt_money

    async with ctx.session() as session:
        promo = await session.get(PromoCode, promo_id)
        if promo is None:
            raise ServiceError("Промокод не найден.", "not_found")
        orders = (await session.execute(select(Order).where(Order.promo_code == promo.code).order_by(Order.created_at.desc()).limit(limit))).scalars().all()
        pays = {}
        if orders:
            for p in (await session.execute(select(Payment).where(Payment.order_id.in_([o.id for o in orders])).order_by(Payment.id))).scalars():
                pays[p.order_id] = p.provider_payment_id
    return [{
        "id": o.id, "user_id": o.user_id, "plan_title": o.plan_title,
        "base_label": fmt_money(o.amount_minor + o.discount_minor, o.currency), "discount_label": fmt_money(o.discount_minor, o.currency),
        "amount_label": fmt_money(o.amount_minor, o.currency), "status": o.status, "status_label": ORDER_STATUS_LABELS.get(o.status, o.status),
        "payment_id": pays.get(o.id), "created_at": ts(o.created_at), "paid_at": ts(o.paid_at),
    } for o in orders]

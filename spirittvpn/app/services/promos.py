"""Promo codes: validation and atomic, replay-safe application."""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivationStatus, PromoActivation, PromoCode, PromoKind, Subscription, User
from app.services.orders import pending_discount
from app.services.users import extend_subscription, has_paid_orders

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


class PromoError(enum.Enum):
    INVALID_FORMAT = "Промокод может содержать только латинские буквы, цифры, «-» и «_» (3–32 символа)."
    NOT_FOUND = "Такого промокода нет. Проверь написание."
    INACTIVE = "Этот промокод сейчас не действует."
    NOT_STARTED = "Этот промокод ещё не начал действовать."
    EXPIRED = "Срок действия промокода истёк."
    LIMIT = "Лимит активаций этого промокода исчерпан."
    NOT_ALLOWED = "Этот промокод недоступен для твоего аккаунта."
    ALREADY_USED = "Ты уже активировал этот промокод."
    DISCOUNT_PENDING = "У тебя уже есть неиспользованная скидка — сначала оформи покупку."
    SERVICE_NOT_READY = "Бонусные дни станут доступны после запуска VPN-серверов. Попробуй позже."


@dataclass
class PromoResult:
    ok: bool
    error: PromoError | None = None
    promo: PromoCode | None = None
    subscription: Subscription | None = None


def normalize_code(raw: str) -> str | None:
    code = raw.strip().upper()
    return code if CODE_RE.match(code) else None


def describe(promo: PromoCode, fmt_money) -> str:
    if promo.kind == PromoKind.DAYS:
        from app.services.formatting import days_word

        text = f"+{days_word(promo.value)} подписки"
    elif promo.kind == PromoKind.PERCENT:
        text = f"скидка {promo.value}% на следующую покупку"
    else:
        text = f"скидка {fmt_money(promo.value)} на следующую покупку"
    return f"{text}" + (f" — {promo.description}" if promo.description else "")


async def activate_promo(
    session: AsyncSession, user: User, raw_code: str, now: datetime, *, vpn_ready: bool
) -> PromoResult:
    code = normalize_code(raw_code)
    if code is None:
        return PromoResult(False, PromoError.INVALID_FORMAT)
    promo = await session.scalar(select(PromoCode).where(PromoCode.code == code))
    if promo is None or promo.is_archived:
        return PromoResult(False, PromoError.NOT_FOUND)
    if not promo.is_active:
        return PromoResult(False, PromoError.INACTIVE)
    if promo.valid_from and promo.valid_from > now:
        return PromoResult(False, PromoError.NOT_STARTED)
    if promo.valid_until and promo.valid_until <= now:
        return PromoResult(False, PromoError.EXPIRED)
    if promo.allowed_group and promo.allowed_group != user.group:
        return PromoResult(False, PromoError.NOT_ALLOWED)
    if promo.new_users_only and await has_paid_orders(session, user.id):
        return PromoResult(False, PromoError.NOT_ALLOWED)
    used = await session.scalar(
        select(PromoActivation.id).where(PromoActivation.promo_id == promo.id, PromoActivation.user_id == user.id)
    )
    if used is not None:
        return PromoResult(False, PromoError.ALREADY_USED)
    if promo.max_activations is not None and promo.activations_count >= promo.max_activations:
        return PromoResult(False, PromoError.LIMIT)
    if promo.kind in PromoKind.DISCOUNTS and await pending_discount(session, user):
        return PromoResult(False, PromoError.DISCOUNT_PENDING)
    if promo.kind == PromoKind.DAYS and not vpn_ready:
        return PromoResult(False, PromoError.SERVICE_NOT_READY)

    # Atomic counter: the WHERE clause guards against exceeding the limit under concurrency.
    res = await session.execute(
        update(PromoCode)
        .where(
            PromoCode.id == promo.id,
            PromoCode.is_active,
            or_(PromoCode.max_activations.is_(None), PromoCode.activations_count < PromoCode.max_activations),
        )
        .values(activations_count=PromoCode.activations_count + 1)
    )
    if res.rowcount != 1:
        await session.rollback()
        return PromoResult(False, PromoError.LIMIT)

    status = ActivationStatus.APPLIED if promo.kind == PromoKind.DAYS else ActivationStatus.PENDING
    session.add(PromoActivation(promo_id=promo.id, user_id=user.id, status=status, created_at=now))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return PromoResult(False, PromoError.ALREADY_USED)

    sub = None
    if promo.kind == PromoKind.DAYS:
        sub = await extend_subscription(session, user, promo.value, now)
    await session.commit()
    await session.refresh(promo)
    return PromoResult(True, promo=promo, subscription=sub)


async def user_activations(session: AsyncSession, user_id: int) -> list[tuple[PromoActivation, PromoCode]]:
    rows = await session.execute(
        select(PromoActivation, PromoCode)
        .join(PromoCode, PromoCode.id == PromoActivation.promo_id)
        .where(PromoActivation.user_id == user_id)
        .order_by(PromoActivation.created_at.desc())
        .limit(20)
    )
    return [(a, p) for a, p in rows.all()]

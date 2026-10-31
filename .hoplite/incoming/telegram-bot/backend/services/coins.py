"""SpirCoin: balance, ledger, emission limits and purchases of subscriptions for coins.

Every balance change locks the user row and writes a CoinTransaction in the same database transaction, so the
balance can never go negative or drift from the ledger. Coin purchases reuse the regular order pipeline
(Order -> apply_paid_order -> fulfill_order) with currency "SPC", so VPN provisioning and its retries are shared.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CoinTransaction, Fulfillment, Order, OrderStatus, Plan, User
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.coins")
COIN_CURRENCY = "SPC"
EMISSION_KINDS = ("task_reward", "admin_grant")
EMISSION_LOCK = 7401
IDEM_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
KIND_LABELS = {
    "task_reward": "Задание",
    "purchase": "Покупка подписки",
    "admin_grant": "Начисление администратором",
    "admin_debit": "Списание администратором",
    "refund": "Возврат",
}


def fmt_coins(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " SpirCoin"


async def balance(session: AsyncSession, user_id: int) -> int:
    return int(await session.scalar(select(User.coin_balance).where(User.id == user_id)) or 0)


async def history(session: AsyncSession, user_id: int, limit: int = 50) -> list[CoinTransaction]:
    stmt = select(CoinTransaction).where(CoinTransaction.user_id == user_id).order_by(CoinTransaction.id.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def _lock_user(session: AsyncSession, user_id: int) -> User | None:
    # NO KEY UPDATE: serialises balance changes but still lets other transactions insert rows referencing the user
    # (devices, orders); a plain FOR UPDATE deadlocks against VPN provisioning running in parallel.
    return await session.scalar(select(User).where(User.id == user_id).with_for_update(key_share=True))


def _period_starts(tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    local = utcnow().astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


async def _check_emission(session: AsyncSession, amount: int, tz_name: str) -> None:
    """Global daily/monthly caps on newly issued coins (0 = no cap). Serialised with an advisory lock."""
    daily = int(await settings_svc.get(session, "coins_daily_emission_limit"))
    monthly = int(await settings_svc.get(session, "coins_monthly_emission_limit"))
    if not daily and not monthly:
        return
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": EMISSION_LOCK})
    day, month = _period_starts(tz_name)
    issued = lambda since: session.scalar(  # noqa: E731
        select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(
            CoinTransaction.kind.in_(EMISSION_KINDS), CoinTransaction.amount > 0, CoinTransaction.created_at >= since
        )
    )
    if daily and int(await issued(day)) + amount > daily:
        raise ServiceError("Дневной лимит выдачи SpirCoin исчерпан. Попробуйте завтра.", "emission_limit")
    if monthly and int(await issued(month)) + amount > monthly:
        raise ServiceError("Месячный лимит выдачи SpirCoin исчерпан.", "emission_limit")


async def apply(
    session: AsyncSession,
    user_id: int,
    amount: int,
    *,
    kind: str,
    actor: str,
    reason: str | None = None,
    task_id: int | None = None,
    order_id: int | None = None,
    idempotency_key: str | None = None,
    tz_name: str = "Europe/Moscow",
) -> CoinTransaction:
    """Change a balance inside the caller's transaction. Raises instead of going below zero."""
    if amount == 0:
        raise ServiceError("Сумма должна быть ненулевой.", "bad_amount")
    user = await _lock_user(session, user_id)
    if user is None:
        raise ServiceError("Пользователь не найден.", "not_found")
    if amount > 0 and kind in EMISSION_KINDS:
        await _check_emission(session, amount, tz_name)
    new_balance = user.coin_balance + amount
    if new_balance < 0:
        raise ServiceError(f"Недостаточно SpirCoin: не хватает {fmt_coins(-new_balance)}.", "insufficient_coins")
    user.coin_balance = new_balance
    tx = CoinTransaction(
        user_id=user_id, amount=amount, balance_after=new_balance, kind=kind, actor=actor, reason=reason,
        task_id=task_id, order_id=order_id, idempotency_key=idempotency_key,
    )
    session.add(tx)
    await session.flush()
    return tx


async def admin_adjust(ctx: AppContext, admin_id: int, source: str, user_id: int, amount: int, reason: str) -> int:
    from backend.services import admin as admin_svc
    from backend.services import roles

    reason = admin_svc.require_reason(reason)
    if not isinstance(amount, int) or amount == 0 or abs(amount) > 10_000_000:
        raise ServiceError("Укажите сумму от 1 до 10 000 000 (со знаком минус — списание).", "bad_amount")
    async with ctx.session() as session, session.begin():
        cap = int(await settings_svc.get(session, "coins_admin_max_per_operation"))
        if cap and abs(amount) > cap and not await roles.is_owner(ctx, admin_id):
            raise ServiceError(f"Лимит одной операции для администратора: {fmt_coins(cap)}.", "admin_cap")
        old = await balance(session, user_id)
        tx = await apply(
            session, user_id, amount, kind="admin_grant" if amount > 0 else "admin_debit", actor=f"admin:{admin_id}",
            reason=reason, tz_name=ctx.settings.display_timezone,
        )
        admin_svc.audit(
            session, admin_id=admin_id, source=source, action="coins.grant" if amount > 0 else "coins.debit",
            entity_type="user", entity_id=user_id, reason=reason, old={"balance": old}, new={"balance": tx.balance_after, "amount": amount},
        )
        new = tx.balance_after
    sign = "начислено" if amount > 0 else "списано"
    await ctx.notifier.send(user_id, f"🪙 SpirCoin: {sign} {fmt_coins(abs(amount))}. Баланс: {fmt_coins(new)}.\nПричина: {reason}")
    return new


@dataclass
class Purchase:
    order_id: int
    balance: int
    replayed: bool
    confirmed: bool
    error: str | None


async def _replay(session: AsyncSession, user_id: int, key: str) -> CoinTransaction | None:
    return await session.scalar(
        select(CoinTransaction).where(CoinTransaction.user_id == user_id, CoinTransaction.idempotency_key == key)
    )


async def purchase(ctx: AppContext, user_id: int, plan_code: str, idempotency_key: str) -> Purchase:
    from backend.services import payments as pay_svc
    from backend.services import referrals as ref_svc

    if not IDEM_RE.match(idempotency_key or ""):
        raise ServiceError("Некорректный ключ запроса. Обновите экран и попробуйте снова.", "bad_request")
    try:
        async with ctx.session() as session, session.begin():
            user = await _lock_user(session, user_id)  # serialises all purchases of this user
            if user is None:
                raise ServiceError("Пользователь не найден.", "not_found")
            prev = await _replay(session, user_id, idempotency_key)
            if prev is not None:
                order_id, bal, replayed = prev.order_id, user.coin_balance, True
            else:
                if user.is_banned:
                    raise ServiceError("Аккаунт заблокирован. Обратитесь в поддержку.", "banned")
                if not await settings_svc.get(session, "coins_enabled"):
                    raise ServiceError("Покупка за SpirCoin временно отключена.", "coins_disabled")
                plan = await session.scalar(select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True)))
                if plan is None or not plan.price_coins:
                    raise ServiceError("Этот тариф нельзя купить за SpirCoin.", "plan_unavailable")
                sub = await subs_svc.get_current(session, user_id, lock=True)
                if sub is not None and sub.is_lifetime:
                    raise ServiceError("У вас уже бессрочная подписка — покупать ещё не нужно.", "lifetime_active")
                price = plan.price_coins  # read under lock: the price the user pays is the price at this moment
                if user.coin_balance < price:
                    raise ServiceError(
                        f"Недостаточно SpirCoin: нужно {fmt_coins(price)}, на балансе {fmt_coins(user.coin_balance)}. "
                        f"Не хватает {fmt_coins(price - user.coin_balance)}.",
                        "insufficient_coins",
                    )
                now = utcnow()
                order = Order(
                    user_id=user_id, plan_id=plan.id, plan_code=plan.code, plan_title=plan.title, duration_days=plan.duration_days,
                    is_lifetime=plan.is_lifetime, device_limit=plan.device_limit, amount_minor=price, currency=COIN_CURRENCY,
                    status=OrderStatus.PAID, paid_at=now, fulfillment_status=Fulfillment.PENDING,
                    referrer_id=await ref_svc.referrer_of(session, user_id),
                )
                session.add(order)
                await session.flush()
                await subs_svc.apply_paid_order(session, order)
                tx = await apply(
                    session, user_id, -price, kind="purchase", actor=f"user:{user_id}", reason=f"Тариф «{plan.title}»",
                    order_id=order.id, idempotency_key=idempotency_key,
                )
                order_id, bal, replayed = order.id, tx.balance_after, False
    except IntegrityError:
        # A concurrent request with the same key won the race: report its result.
        async with ctx.session() as session:
            prev = await _replay(session, user_id, idempotency_key)
            if prev is None:
                raise
            order_id, bal, replayed = prev.order_id, await balance(session, user_id), True
    confirmed = await pay_svc.fulfill_order(ctx, order_id)  # VPN side; the worker retries on failure
    async with ctx.session() as session:
        order = await session.get(Order, order_id)
        error = order.fulfillment_error if order is not None and not confirmed else None
    return Purchase(order_id=order_id, balance=bal, replayed=replayed, confirmed=confirmed, error=error)


async def economy_stats(session: AsyncSession) -> dict:
    from backend.models import Task, TaskClaim

    s = lambda stmt: session.scalar(stmt)  # noqa: E731
    return {
        "paid_orders": int(await s(select(func.count(Order.id)).where(Order.status == OrderStatus.PAID, Order.currency != COIN_CURRENCY)) or 0),
        "coin_orders": int(await s(select(func.count(Order.id)).where(Order.status == OrderStatus.PAID, Order.currency == COIN_CURRENCY)) or 0),
        "tasks_rewarded": int(await s(select(func.count(TaskClaim.id)).where(TaskClaim.status == "rewarded")) or 0),
        "claims_pending": int(await s(select(func.count(TaskClaim.id)).where(TaskClaim.status == "pending")) or 0),
        "coins_issued": int(await s(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(CoinTransaction.amount > 0, CoinTransaction.kind != "refund")) or 0),
        "coins_spent": int(-(await s(select(func.coalesce(func.sum(CoinTransaction.amount), 0)).where(CoinTransaction.kind == "purchase")) or 0)),
        "coins_in_circulation": int(await s(select(func.coalesce(func.sum(User.coin_balance), 0))) or 0),
        "tasks_active": int(await s(select(func.count(Task.id)).where(Task.is_active.is_(True), Task.archived_at.is_(None))) or 0),
    }


async def recent_transactions(session: AsyncSession, *, user_id: int | None = None, limit: int = 100) -> list[tuple[CoinTransaction, User]]:
    stmt = select(CoinTransaction, User).join(User, User.id == CoinTransaction.user_id).order_by(CoinTransaction.id.desc()).limit(max(1, min(limit, 500)))
    if user_id is not None:
        stmt = stmt.where(CoinTransaction.user_id == user_id)
    return list((await session.execute(stmt)).all())


__all__ = ["COIN_CURRENCY", "KIND_LABELS", "Purchase", "apply", "admin_adjust", "balance", "economy_stats", "fmt_coins", "history", "purchase", "recent_transactions"]

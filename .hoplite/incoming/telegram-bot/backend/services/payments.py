"""Orders and payments. Access is granted only after the provider API confirms the payment."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.payments import PaymentError, PaymentState, PaymentStatus, WebhookRejected
from backend.models import (
    Device,
    DevStatus,
    Fulfillment,
    Order,
    OrderStatus,
    Payment,
    PaymentEvent,
    Plan,
    User,
)
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_dt, fmt_money, utcnow

log = logging.getLogger("spiritt.payments")


async def list_plans(session: AsyncSession, *, only_active: bool = True) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.sort_order, Plan.id)
    if only_active:
        stmt = stmt.where(Plan.is_active.is_(True))
    return list((await session.execute(stmt)).scalars())


def plan_terms(plan: Plan | Order) -> str:
    devices = f"{plan.device_limit} {'устройство' if plan.device_limit == 1 else 'устройства' if plan.device_limit < 5 else 'устройств'}"
    if plan.is_lifetime:
        return f"Бессрочно, без продлений · {devices}"
    return f"{plan.duration_days} дней · {devices}"


async def create_order(ctx: AppContext, user_id: int, plan_code: str) -> tuple[Order, Payment]:
    if ctx.payments is None:
        raise ServiceError("Оплата временно недоступна: платёжная система ещё не подключена.", "payments_disabled")
    now = utcnow()
    async with ctx.session() as session, session.begin():
        if not await settings_svc.get(session, "purchases_enabled"):
            raise ServiceError("Продажи временно приостановлены. Попробуйте позже.", "purchases_disabled")
        user = await session.get(User, user_id)
        if user is None or user.is_banned:
            raise ServiceError("Покупка недоступна для этого аккаунта.", "banned")
        plan = await session.scalar(select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True)))
        if plan is None:
            raise ServiceError("Тариф недоступен.", "bad_plan")
        sub = await subs_svc.get_current(session, user_id)
        if sub is not None and sub.is_lifetime:
            raise ServiceError("У вас уже бессрочная подписка — продление не требуется.", "has_lifetime")
        if sub is not None:
            used = await dev_svc.occupied_slots(session, sub.id)
            if used > plan.device_limit:
                raise ServiceError(
                    f"Сейчас подключено {used} устройств, а тариф допускает {plan.device_limit}. Сначала отключите лишние устройства.",
                    "too_many_devices",
                )
        recent = await session.scalar(select(func.count(Order.id)).where(Order.user_id == user_id, Order.created_at > now - timedelta(hours=1)))
        if int(recent or 0) >= 10:
            raise ServiceError("Слишком много заказов за час. Попробуйте позже.", "rate_limited")
        from backend.services import promo as promo_svc

        discount = await promo_svc.active_discount(session, user_id)
        discount_minor = promo_svc.discount_for(plan.price_minor, discount[1]) if discount else 0
        amount_minor = plan.price_minor - discount_minor
        # Reuse a fresh unpaid order for the same plan instead of creating duplicates.
        existing = await session.scalar(
            select(Order)
            .where(
                Order.user_id == user_id,
                Order.plan_id == plan.id,
                Order.status == OrderStatus.PENDING,
                Order.amount_minor == amount_minor,
                Order.created_at > now - timedelta(minutes=30),
            )
            .order_by(Order.created_at.desc())
        )
        if existing is not None:
            pay = next((p for p in existing.payments if p.confirmation_url and p.status == PaymentStatus.PENDING), None)
            if pay is not None:
                return existing, pay
        order = Order(
            user_id=user_id,
            plan_id=plan.id,
            plan_code=plan.code,
            plan_title=plan.title,
            duration_days=plan.duration_days,
            is_lifetime=plan.is_lifetime,
            device_limit=plan.device_limit,
            amount_minor=amount_minor,
            discount_minor=discount_minor,
            promo_activation_id=discount[0].id if discount and discount_minor else None,
            promo_code=discount[1].code if discount and discount_minor else None,
            referrer_id=await ref_svc.referrer_of(session, user_id),
            currency=plan.currency,
        )
        session.add(order)
        await session.flush()
        order_id, public_id = order.id, str(order.public_id)
        description = f"SpirittVPN — {plan.title}" + (f" (промокод {discount[1].code})" if discount and discount_minor else "")

    try:
        created = await ctx.payments.create_payment(
            amount_minor=order.amount_minor,
            currency=order.currency,
            description=description,
            order_public_id=public_id,
            idempotency_key=public_id,
        )
    except PaymentError as exc:
        async with ctx.session() as session, session.begin():
            o = await session.get(Order, order_id, with_for_update=True)
            o.status = OrderStatus.FAILED
        log.warning("create payment failed for order %s: %s", order_id, exc)
        raise ServiceError("Не удалось создать платёж. Попробуйте позже.", "payment_create_failed") from exc

    if created.amount_minor != order.amount_minor or created.currency != order.currency:
        async with ctx.session() as session, session.begin():
            o = await session.get(Order, order_id, with_for_update=True)
            o.status = OrderStatus.FAILED
        raise ServiceError("Платёжная система вернула неверную сумму. Платёж отменён.", "amount_mismatch")
    async with ctx.session() as session, session.begin():
        o = await session.get(Order, order_id, with_for_update=True)
        pay = Payment(
            order_id=o.id,
            provider=ctx.payments.name,
            provider_payment_id=created.provider_payment_id,
            status=created.status,
            amount_minor=created.amount_minor,
            currency=created.currency,
            confirmation_url=created.confirmation_url,
            is_test=created.is_test,
        )
        session.add(pay)
        o.status = OrderStatus.PENDING
    return o, pay


async def _record_event(session: AsyncSession, provider: str, pid: str, event: str, source: str, result: str) -> bool:
    res = await session.execute(
        insert(PaymentEvent)
        .values(provider=provider, provider_payment_id=pid, event=event, source=source, result=result)
        .on_conflict_do_nothing(constraint="uq_payment_events_once")
        .returning(PaymentEvent.id)
    )
    return res.scalar_one_or_none() is not None


async def handle_state(ctx: AppContext, provider: str, state: PaymentState, *, source: str) -> str:
    """Apply an authoritative payment state. Safe to call any number of times, concurrently."""
    newly_paid_order: int | None = None
    async with ctx.session() as session, session.begin():
        pay = await session.scalar(
            select(Payment).where(Payment.provider == provider, Payment.provider_payment_id == state.provider_payment_id).with_for_update()
        )
        if pay is None:
            await _record_event(session, provider, state.provider_payment_id, state.status, source, "unknown_payment")
            log.warning("payment %s not found", state.provider_payment_id)
            return "unknown_payment"
        order = await session.get(Order, pay.order_id, with_for_update=True)

        if state.status == PaymentStatus.SUCCEEDED and state.paid:
            if order.status in (OrderStatus.PAID, OrderStatus.REFUNDED):
                await _record_event(session, provider, pay.provider_payment_id, "succeeded", source, "duplicate")
                return "duplicate"
            meta_order = state.metadata.get("order_id")
            if state.amount_minor != order.amount_minor or state.currency != order.currency or (meta_order and meta_order != str(order.public_id)):
                pay.status, pay.error = "mismatch", f"amount={state.amount_minor} {state.currency} meta={meta_order}"
                await _record_event(session, provider, pay.provider_payment_id, "succeeded", source, "mismatch")
                log.error("payment %s mismatch for order %s", pay.provider_payment_id, order.id)
                result = "mismatch"
            elif state.is_test and ctx.settings.app_env == "production":
                pay.status, pay.error = "test_rejected", "Тестовый платёж в production"
                await _record_event(session, provider, pay.provider_payment_id, "succeeded", source, "test_rejected")
                result = "test_rejected"
            else:
                now = utcnow()
                is_first = not await session.scalar(
                    select(func.count(Order.id)).where(
                        Order.user_id == order.user_id, Order.status.in_((OrderStatus.PAID, OrderStatus.REFUNDED)), Order.id != order.id,
                        Order.currency != "SPC",  # coin purchases don't count as a first real payment
                    )
                )
                order.status, order.paid_at, order.fulfillment_status = OrderStatus.PAID, now, Fulfillment.PENDING
                pay.status, pay.confirmed_at, pay.is_test, pay.error = PaymentStatus.SUCCEEDED, now, state.is_test, None
                await subs_svc.apply_paid_order(session, order)
                if order.promo_activation_id:
                    from backend.models import PromoActivation

                    act = await session.get(PromoActivation, order.promo_activation_id, with_for_update=True)
                    if act is not None and act.status == "reserved":
                        act.status, act.used_at, act.order_id = "used", now, order.id
                if is_first:
                    await ref_svc.grant_reward(session, order.user_id, "first_payment", order_id=order.id)
                await _record_event(session, provider, pay.provider_payment_id, "succeeded", source, "paid")
                newly_paid_order = order.id
                result = "paid"
        elif state.status == PaymentStatus.CANCELED:
            pay.status, pay.error = PaymentStatus.CANCELED, state.cancel_reason
            if order.status in (OrderStatus.CREATED, OrderStatus.PENDING):
                order.status = OrderStatus.CANCELED
            await _record_event(session, provider, pay.provider_payment_id, "canceled", source, "canceled")
            result = "canceled"
        else:
            pay.status = state.status
            result = "pending"
    if result in ("mismatch", "test_rejected"):
        await notify_admins(ctx, f"⚠️ Платёж {state.provider_payment_id}: {result}. Доступ не выдан, проверьте вручную.")
    if newly_paid_order is not None:
        await fulfill_order(ctx, newly_paid_order)
    return result


async def handle_webhook(ctx: AppContext, *, body: bytes, headers: Mapping[str, str], client_ip: str | None) -> str:
    if ctx.payments is None:
        raise WebhookRejected("Платёжный провайдер не настроен")
    event = ctx.payments.parse_webhook(body=body, headers=headers, client_ip=client_ip)
    # The notification body is never trusted: re-read the payment with our credentials.
    state = await ctx.payments.get_payment(event.provider_payment_id)
    if event.event == "refund.succeeded":
        return await mark_refunded(ctx, ctx.payments.name, event.provider_payment_id)
    return await handle_state(ctx, ctx.payments.name, state, source="webhook")


async def mark_refunded(ctx: AppContext, provider: str, pid: str) -> str:
    async with ctx.session() as session, session.begin():
        pay = await session.scalar(select(Payment).where(Payment.provider == provider, Payment.provider_payment_id == pid).with_for_update())
        if pay is None:
            return "unknown_payment"
        order = await session.get(Order, pay.order_id, with_for_update=True)
        if not await _record_event(session, provider, pid, "refund.succeeded", "webhook", "refunded"):
            return "duplicate"
        pay.status = PaymentStatus.REFUNDED
        if order.status == OrderStatus.PAID:
            order.status = OrderStatus.REFUNDED
        user_id = order.user_id
    await notify_admins(ctx, f"↩️ Возврат по платежу {pid} (пользователь {user_id}). Подписку при необходимости приостановите вручную.")
    return "refunded"


async def refresh_order(ctx: AppContext, user_id: int, order_id: int) -> Order:
    """User-triggered status check: always asks the provider, never trusts the client."""
    async with ctx.session() as session:
        order = await session.get(Order, order_id)
        if order is None or order.user_id != user_id:
            raise ServiceError("Заказ не найден.", "not_found")
        pending = [p for p in order.payments if p.status in (PaymentStatus.PENDING, PaymentStatus.WAITING_FOR_CAPTURE)]
    if ctx.payments is not None and order.status == OrderStatus.PENDING:
        for p in pending:
            try:
                state = await ctx.payments.get_payment(p.provider_payment_id)
            except PaymentError as exc:
                raise ServiceError("Платёжная система не ответила. Попробуйте через минуту.", "provider_unavailable") from exc
            await handle_state(ctx, p.provider, state, source="manual")
    async with ctx.session() as session:
        return await session.get(Order, order_id, populate_existing=True)


async def fulfill_order(ctx: AppContext, order_id: int, *, notify: bool = True) -> bool:
    """Create/extend VPN clients for a paid order. Repeatable; the worker retries failures."""
    async with ctx.session() as session:
        order = await session.get(Order, order_id)
        if order is None or order.status != OrderStatus.PAID or order.fulfillment_status == Fulfillment.DONE:
            return order is not None and order.fulfillment_status == Fulfillment.DONE
        sub = await subs_svc.get_current(session, order.user_id)
        auto = bool(await settings_svc.get(session, "auto_provision_first_device"))
        devices = list(
            (await session.execute(select(Device).where(Device.subscription_id == sub.id, Device.status.in_(DevStatus.OCCUPYING)))).scalars()
        ) if sub else []
        prev_status = order.fulfillment_status
    error: str | None = None
    ok = False
    if sub is None:
        error = "Подписка не найдена"
    elif sub.status == "suspended":
        ok, error = True, None
    else:
        try:
            if not devices and auto:
                res = await dev_svc.add_device(ctx, order.user_id, label="Устройство 1", actor="system")
                ok, error = res.confirmed, res.error
            else:
                _, failed = await dev_svc.sync_subscription(ctx, sub.id)
                async with ctx.session() as session:
                    pending = await session.scalar(
                        select(func.count(Device.id)).where(Device.subscription_id == sub.id, Device.status == DevStatus.PENDING)
                    )
                ok = failed == 0 and not pending
                error = None if ok else "Не все устройства подтверждены сервером"
        except ServiceError as exc:
            error = exc.message
    async with ctx.session() as session, session.begin():
        o = await session.get(Order, order_id, with_for_update=True)
        o.fulfillment_status = Fulfillment.DONE if ok else Fulfillment.VPN_FAILED
        o.fulfillment_error = error
        o.fulfilled_at = utcnow() if ok else None
        sub = await subs_svc.get_current(session, o.user_id)
        until = "бессрочно" if sub and sub.is_lifetime else f"до {fmt_dt(sub.expires_at) if sub else '—'}"
    if notify and (ok or prev_status == Fulfillment.PENDING):
        if ok:
            text = (
                f"✅ <b>Оплата подтверждена</b>\n\nТариф «{order.plan_title}» активен {until}.\n"
                "Откройте «📱 Мои устройства», чтобы получить ссылку подключения и QR-код."
            )
        else:
            text = (
                f"💳 <b>Оплата получена</b>, подписка «{order.plan_title}» оформлена {until}.\n\n"
                "⚠️ VPN-сервер пока не подтвердил создание подключения. Мы повторим автоматически и сообщим о результате."
            )
        await ctx.notifier.send(order.user_id, text)
    if not ok:
        log.warning("fulfillment of order %s failed: %s", order_id, error)
    return ok


async def poll_pending(ctx: AppContext, limit: int = 50) -> int:
    if ctx.payments is None:
        return 0
    now = utcnow()
    async with ctx.session() as session:
        rows = (
            await session.execute(
                select(Payment.provider, Payment.provider_payment_id)
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Payment.provider == ctx.payments.name,
                    Payment.status.in_((PaymentStatus.PENDING, PaymentStatus.WAITING_FOR_CAPTURE)),
                    Payment.created_at > now - timedelta(days=2),
                    Payment.updated_at < now - timedelta(seconds=45),
                )
                .limit(limit)
            )
        ).all()
    n = 0
    for provider, pid in rows:
        try:
            state = await ctx.payments.get_payment(pid)
            await handle_state(ctx, provider, state, source="poll")
            n += 1
        except PaymentError as exc:
            log.info("poll %s failed: %s", pid, exc)
            async with ctx.session() as session, session.begin():
                p = await session.scalar(select(Payment).where(Payment.provider_payment_id == pid).with_for_update())
                p.updated_at = utcnow()
    return n


async def retry_fulfillment(ctx: AppContext) -> int:
    async with ctx.session() as session:
        ids = (
            await session.execute(
                select(Order.id).where(
                    Order.status == OrderStatus.PAID,
                    Order.fulfillment_status.in_((Fulfillment.PENDING, Fulfillment.VPN_FAILED)),
                    Order.updated_at < utcnow() - timedelta(minutes=2),
                )
            )
        ).scalars().all()
    for oid in ids:
        await fulfill_order(ctx, oid)
    return len(ids)


async def user_orders(session: AsyncSession, user_id: int, limit: int = 20) -> list[Order]:
    return list(
        (await session.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(limit))).scalars()
    )


ORDER_STATUS_LABELS = {
    OrderStatus.CREATED: "🕓 Создан",
    OrderStatus.PENDING: "⏳ Ожидает оплаты",
    OrderStatus.PAID: "✅ Оплачен",
    OrderStatus.CANCELED: "✖️ Отменён",
    OrderStatus.FAILED: "⚠️ Ошибка",
    OrderStatus.REFUNDED: "↩️ Возврат",
}


def order_line(order: Order) -> str:
    return f"{ORDER_STATUS_LABELS.get(order.status, order.status)} · {order.plan_title} · {fmt_money(order.amount_minor, order.currency)} · {fmt_dt(order.created_at)}"


async def notify_admins(ctx: AppContext, text: str) -> None:
    from backend.services import roles

    for admin_id in sorted(await roles.admin_ids(ctx)):
        try:
            await ctx.notifier.send(admin_id, text)
        except Exception:  # noqa: BLE001
            log.exception("admin notify failed")

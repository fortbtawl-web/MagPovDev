"""Purchase flow: plans → terms → order → payment → confirmation (only via provider)."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app import texts
from app.handlers.common import render
from app.models import Order, User
from app.payments.providers import TelegramPaymentProvider
from app.runtime import Deps, deliver_payment_result, notify_admins
from app.services.formatting import days_word, fmt_money
from app.services.orders import (
    active_plans,
    create_order,
    get_active_plan,
    mark_failed,
    mark_pending,
    process_payment_event,
    quote,
    validate_for_checkout,
)
from app.services.users import has_accepted_terms

router = Router(name="buy")
log = logging.getLogger("spirittvpn.buy")


def now_utc() -> datetime:
    return datetime.now(UTC)


@router.callback_query(F.data == "buy")
async def cb_buy(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    if not deps.vpn.is_configured:
        await render(cb, texts.SERVICE_NOT_READY_BUY, kb.back_to("menu"))
        return
    plans = await active_plans(session)
    if not plans:
        await render(cb, texts.NO_PLANS, kb.back_to("menu"))
        return
    lines = [texts.PLANS_HEADER]
    buttons = []
    for p in plans:
        q = await quote(session, user, p)
        price = fmt_money(q.total, p.currency)
        old = f" <s>{fmt_money(q.original, p.currency)}</s>" if q.discount else ""
        lines.append(f"<b>{escape(p.title)}</b> · {days_word(p.duration_days)} · {price}{old}\n<i>{escape(p.description)}</i>")
        buttons.append((p.code, f"{p.title} — {price}"))
    lines.append("\nПродление добавляется к текущему сроку подписки.")
    if deps.payments.is_test:
        lines.append("🧪 <i>Тестовый режим оплаты: реальные деньги не списываются.</i>")
    await render(cb, "\n\n".join(lines), kb.plans(buttons), logo=deps.logo)


async def show_plan(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User, code: str) -> None:
    plan = await get_active_plan(session, code)
    if plan is None:
        await render(cb, "Этот тариф сейчас недоступен.", kb.back_to("buy", "← К тарифам"))
        return
    q = await quote(session, user, plan)
    lines = [
        f"💳 <b>{escape(plan.title)}</b>\n{texts.DIV}",
        f"⏱ Длительность: {days_word(plan.duration_days)}",
        f"💰 Стоимость: {fmt_money(q.original, plan.currency)}",
        f"📝 {escape(plan.description)}" if plan.description else "",
    ]
    if q.discount:
        lines.append(f"🎟️ Скидка по промокоду {escape(q.promo_code or '')}: −{fmt_money(q.discount, plan.currency)}")
    lines.append(f"\n<b>Итого к оплате: {fmt_money(q.total, plan.currency)}</b>")
    body = "\n".join(line for line in lines if line)
    if not deps.payments.is_enabled:
        await render(cb, body + "\n\n" + texts.PAYMENTS_UNAVAILABLE, kb.back_to("buy", "← К тарифам"))
        return
    if not await has_accepted_terms(session, user, deps.settings.legal_docs_version):
        gate = texts.TERMS_REQUIRED.format(action="перейти к оплате")
        await render(cb, body + "\n\n" + gate, kb.terms_gate(f"plan:{plan.code}", "buy"))
        return
    body += (f'\n\nОплачивая, ты соглашаешься с <a href="{texts.b.USER_AGREEMENT_URL}">пользовательским соглашением</a> '
             f'и <a href="{texts.b.PRIVACY_POLICY_URL}">политикой конфиденциальности</a>.')
    await render(cb, body, kb.plan_checkout(plan.code, f"💳 Оплатить {fmt_money(q.total, plan.currency)}"))


@router.callback_query(F.data.startswith("plan:"))
async def cb_plan(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    await show_plan(cb, session, deps, user, cb.data.split(":", 1)[1][:32])


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    code = cb.data.split(":", 1)[1][:32]
    # Every precondition is re-checked server-side: the button itself proves nothing.
    if not deps.vpn.is_configured:
        await cb.answer()
        await render(cb, texts.SERVICE_NOT_READY_BUY, kb.back_to("menu"))
        return
    if not deps.payments.is_enabled:
        await cb.answer(texts.PAYMENTS_UNAVAILABLE, show_alert=True)
        return
    if not await has_accepted_terms(session, user, deps.settings.legal_docs_version):
        await cb.answer()
        await show_plan(cb, session, deps, user, code)
        return
    order = await create_order(session, user, code, deps.payments.name, deps.payments.is_test, now_utc())
    if order is None:
        await cb.answer("Тариф недоступен", show_alert=True)
        return
    await cb.answer()
    try:
        ps = await deps.payments.create_payment(order)
    except Exception:  # noqa: BLE001 - provider errors must not leak details to the user
        log.exception("create_payment failed order=%s", order.id)
        await mark_failed(session, order.id, "payment session error")
        await render(cb, "⚠️ Не удалось создать платёж. Попробуй ещё раз чуть позже.", kb.back_to("buy", "← К тарифам"))
        return

    note = ("Подписка активируется <b>автоматически</b> сразу после подтверждения оплаты платёжной системой. "
            "Нажимать «Я оплатил» не нужно.")
    if ps.kind == "invoice" and ps.invoice:
        await mark_pending(session, order)
        await render(cb, f"🧾 <b>Заказ</b> <code>{order.id}</code>\n\nСчёт отправлен ниже 👇\n\n{note}", kb.order_status(order.id))
        await bot.send_invoice(cb.from_user.id, **ps.invoice)
        return
    if ps.url:
        await mark_pending(session, order)
        test = "🧪 <b>ТЕСТОВЫЙ ПЛАТЁЖ — деньги не списываются</b>\n\n" if order.is_test else ""
        await render(cb, f"{test}🧾 <b>Заказ</b> <code>{order.id}</code>\n💰 К оплате: {fmt_money(order.amount, order.currency)}\n\n{note}",
                     kb.payment_link(ps.url, order.id, order.is_test))
        return
    await mark_pending(session, order)
    hint = f"\n\nАдминистратор может подтвердить тестовую оплату командой <code>/testpay {order.id} success</code>." if order.is_test else ""
    await render(cb, f"🧾 <b>Заказ</b> <code>{order.id}</code> создан.{hint}", kb.order_status(order.id))


@router.callback_query(F.data.startswith("order:"))
async def cb_order(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    order = await session.get(Order, cb.data.split(":", 1)[1][:36])
    if order is None or order.user_id != user.id:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await session.refresh(order)
    await cb.answer(f"Статус: {texts.ORDER_STATUS.get(order.status, order.status)}", show_alert=True)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, session: AsyncSession, deps: Deps):
    ok = isinstance(deps.payments, TelegramPaymentProvider) and await validate_for_checkout(
        session, query.invoice_payload, query.from_user.id, query.total_amount, query.currency,
        deps.settings.order_ttl_hours, now_utc(),
    )
    if ok:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Заказ устарел или недействителен. Оформи новый заказ в боте.")


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot, session: AsyncSession, deps: Deps):
    sp = message.successful_payment
    assert sp is not None
    event = TelegramPaymentProvider.event_from_successful_payment(sp)
    for attempt in range(5):
        try:
            result = await process_payment_event(session, event, now_utc())
            break
        except (OperationalError, DBAPIError):
            await session.rollback()
            log.exception("db error while applying payment order=%s attempt=%d", event.order_id, attempt + 1)
            await asyncio.sleep(2 ** attempt)
    else:
        # Money is confirmed by Telegram but the DB is down: keep the evidence and alert humans.
        await notify_admins(bot, deps.settings,
                            f"🚨 Оплата не записана (БД недоступна). Заказ <code>{event.order_id}</code>, "
                            f"charge <code>{event.event_id}</code>. Подтверди: <code>/confirm_payment {event.order_id} {event.event_id}</code>")
        try:
            await message.answer("✅ Платёж получен. Активация задерживается из-за технических работ — "
                                 "поддержка уже уведомлена, подписка будет начислена.")
        except TelegramAPIError:
            pass
        return
    await deliver_payment_result(bot, session, deps, result)

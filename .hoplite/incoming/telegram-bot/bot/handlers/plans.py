"""Plans, orders and payment status."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from backend.models import Plan
from backend.services import payments as pay_svc
from backend.services import promo as promo_svc
from backend.services.context import AppContext
from backend.utils import fmt_money
from bot import ui
from bot.common import show
from bot.keyboards import Nav, OrderCb, PlanCb
from bot.middlewares import user_locks
from sqlalchemy import select

router = Router(name="plans")


@router.callback_query(Nav.filter(F.to == "plans"))
async def plans(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        items = await pay_svc.list_plans(session)
    rows = [
        [InlineKeyboardButton(text=f"{'♾ ' if p.is_lifetime else ''}{p.title}" + (f" · {p.price_coins:,} 🪙".replace(",", " ") if p.price_coins else f" · {fmt_money(p.price_minor, p.currency)}"), callback_data=PlanCb(action="view", code=p.code).pack())]
        for p in items
    ]
    blocks = [ui.title("Тарифы", "Выберите подходящий план")]
    if items:
        blocks.append(ui.card(*(f"<b>{ui.safe(p.title)}</b>  {fmt_money(p.price_minor, p.currency)} · {pay_svc.plan_terms(p)}" for p in items)))
    else:
        blocks.append(ui.card("○ Доступных тарифов пока нет"))
    if ctx.payments is None:
        blocks.append(ui.note("Сейчас подписку можно купить за SpirCoin — монеты начисляют за задания. Оплата картой пока не подключена."))
    rows.append([InlineKeyboardButton(text="🎯 Заработать SpirCoin", callback_data=Nav(to="tasks").pack())])
    async with ctx.session() as session:
        found = await promo_svc.active_discount(session, cb.from_user.id)
    if found:
        blocks.append(ui.card(ui.kv("Промокод", f"<code>{ui.safe(found[1].code)}</code>"), ui.kv("Скидка", promo_svc.value_label(found[1].kind, found[1].value)), "Применится к заказу автоматически."))
    else:
        rows.append([InlineKeyboardButton(text="🎟 Есть промокод?", callback_data=Nav(to="promo").pack())])
    rows.append(ui.nav_row())
    await show(cb, ui.screen(*blocks), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(PlanCb.filter(F.action == "view"))
async def plan_view(cb: CallbackQuery, callback_data: PlanCb, ctx: AppContext) -> None:
    async with ctx.session() as session:
        p = await session.scalar(select(Plan).where(Plan.code == callback_data.code, Plan.is_active.is_(True)))
    if p is None:
        await cb.answer("Тариф больше недоступен", show_alert=True)
        return
    async with ctx.session() as session:
        found = await promo_svc.active_discount(session, cb.from_user.id)
    discount = promo_svc.discount_for(p.price_minor, found[1]) if found else 0
    price = f"<s>{fmt_money(p.price_minor, p.currency)}</s> <b>{fmt_money(p.price_minor - discount, p.currency)}</b>" if discount else f"<b>{fmt_money(p.price_minor, p.currency)}</b>"
    lines = [ui.kv("Стоимость", price), ui.kv("Срок", pay_svc.plan_terms(p))]
    if discount:
        lines.append(ui.kv("Промокод", f"<code>{ui.safe(found[1].code)}</code>"))
    rows = []
    from bot.handlers.spircoin import coin_plan_button

    coin_btn = await coin_plan_button(ctx, p)
    if coin_btn is not None:
        rows.append([coin_btn])
        lines.append(ui.kv("За SpirCoin", f"<b>{p.price_coins:,}</b>".replace(",", " ")))
    blocks = [ui.title(p.title), p.description or "", ui.card(*lines)]
    if p.is_lifetime:
        blocks.append(ui.note("Бессрочно: без регулярных продлений и списаний. Работает, пока работает сервис."))
    blocks.append(ui.note("Доступ выдаётся автоматически после оплаты."))
    if ctx.payments is not None:
        rows.append([InlineKeyboardButton(text="✓ Оформить заказ", callback_data=PlanCb(action="buy", code=p.code).pack())])
    else:
        blocks.append(ui.warn("Оплата картой пока не подключена."))
    rows.append(ui.nav_row("plans", "‹ Тарифы"))
    await show(cb, ui.screen(*blocks), InlineKeyboardMarkup(inline_keyboard=rows))


def order_kb(order) -> InlineKeyboardMarkup:
    rows = []
    pay = max(order.payments, key=lambda x: x.id) if order.payments else None
    if order.status == "pending" and pay and pay.confirmation_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=pay.confirmation_url)])
        rows.append([InlineKeyboardButton(text="↻ Проверить оплату", callback_data=OrderCb(action="check", id=order.id).pack())])
    rows.append(ui.nav_row("history", "‹ Платежи"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(PlanCb.filter(F.action == "buy"))
async def plan_buy(cb: CallbackQuery, callback_data: PlanCb, ctx: AppContext) -> None:
    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Заказ уже создаётся…")
        return
    async with lock:
        order, _ = await pay_svc.create_order(ctx, cb.from_user.id, callback_data.code)
        async with ctx.session() as session:
            from backend.models import Order

            order = await session.get(Order, order.id)
    amount = fmt_money(order.amount_minor, order.currency)
    rows_ = [ui.kv("Тариф", ui.safe(order.plan_title)), ui.kv("Сумма", f"<b>{amount}</b>")]
    if order.discount_minor:
        rows_.append(ui.kv("Скидка", fmt_money(order.discount_minor, order.currency)))
    text = ui.screen(
        ui.title(f"Заказ №{order.id}", "Ожидает оплаты"),
        ui.card(*rows_),
        ui.steps(["Нажмите «Оплатить»", "После оплаты доступ выдаётся автоматически — обычно в течение минуты"]),
        ui.note("Если сообщение не пришло, нажмите «Проверить оплату»."),
    )
    await show(cb, text, order_kb(order))


@router.callback_query(OrderCb.filter(F.action == "check"))
async def order_check(cb: CallbackQuery, callback_data: OrderCb, ctx: AppContext) -> None:
    order = await pay_svc.refresh_order(ctx, cb.from_user.id, callback_data.id)
    blocks = [ui.title(f"Заказ №{order.id}"), ui.card(pay_svc.order_line(order))]
    if order.status == "paid":
        blocks.append(ui.ok("Оплата подтверждена"))
        if order.fulfillment_status == "vpn_failed":
            blocks.append(ui.warn("VPN-сервер пока не подтвердил выдачу доступа — повторяем автоматически."))
    elif order.status == "pending":
        blocks.append(ui.note("Оплата ещё не подтверждена платёжной системой."))
    text = ui.screen(*blocks)
    await show(cb, text, order_kb(order))


@router.callback_query(Nav.filter(F.to == "history"))
async def history(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        orders = await pay_svc.user_orders(session, cb.from_user.id, limit=10)
    if not orders:
        text = ui.screen(ui.title("Платежи"), ui.card("○ Платежей пока нет"))
    else:
        text = ui.screen(ui.title("Платежи", "Последние заказы"), ui.card(*(f"№{o.id} · {pay_svc.order_line(o)}" for o in orders)))
    rows = [
        [InlineKeyboardButton(text=f"↻ Проверить заказ №{o.id}", callback_data=OrderCb(action="check", id=o.id).pack())]
        for o in orders
        if o.status == "pending"
    ][:3]
    rows.append(ui.nav_row())
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))

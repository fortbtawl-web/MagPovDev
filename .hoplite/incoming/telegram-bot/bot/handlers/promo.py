"""Промокод."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.services import promo as promo_svc
from backend.services.context import AppContext
from bot import ui
from bot.common import show
from bot.keyboards import Nav
from bot.middlewares import user_locks

router = Router(name="promo")


class PromoFSM(StatesGroup):
    code = State()


async def promo_screen(ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with ctx.session() as session:
        found = await promo_svc.active_discount(session, user_id)
    blocks = [ui.title("Промокод"), "Отправьте промокод одним сообщением."]
    if found:
        blocks.append(ui.card(ui.kv("Активная скидка", f"<code>{ui.safe(found[1].code)}</code> · {promo_svc.value_label(found[1].kind, found[1].value)}"), "Применится к следующему заказу."))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=Nav(to="menu").pack())]])
    return ui.screen(*blocks), kb


@router.callback_query(Nav.filter(F.to == "promo"))
async def promo_open(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.set_state(PromoFSM.code)
    await show(cb, *await promo_screen(ctx, cb.from_user.id))


@router.message(Command("promo"))
async def promo_cmd(message: Message, ctx: AppContext, state: FSMContext) -> None:
    await state.set_state(PromoFSM.code)
    text, kb = await promo_screen(ctx, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(StateFilter(PromoFSM.code), F.text)
async def promo_value(message: Message, ctx: AppContext, state: FSMContext) -> None:
    if (message.text or "").startswith("/"):
        await state.clear()
        raise SkipHandler  # let the command reach its own handler instead of being swallowed
    lock = user_locks.get(message.from_user.id)
    if lock.locked():
        await message.answer(ui.note("Промокод уже проверяется…"))
        return
    async with lock:
        res = await promo_svc.activate(ctx, message.from_user.id, message.text or "")
    await state.clear()
    rows = []
    if res.kind in ("percent", "fixed"):
        rows.append([InlineKeyboardButton(text="💎 Выбрать тариф", callback_data=Nav(to="plans").pack())])
    else:
        rows.append([InlineKeyboardButton(text="🛡 Подписка", callback_data=Nav(to="sub").pack())])
    rows.append(ui.nav_row())
    await message.answer(ui.screen(ui.title("Промокод"), res.message), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

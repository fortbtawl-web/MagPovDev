"""🔑 Test subscription by secret code (separate from the regular trial)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.services import trial_codes as tc_svc
from backend.services.context import AppContext, ServiceError
from bot import ui
from bot.common import show
from bot.keyboards import DevCb, Nav
from bot.middlewares import user_locks

router = Router(name="trial_code")
PROMPT = ui.screen(
    ui.title("Код тестовой подписки", "Тестовая VPN-подписка на 5 дней"),
    ui.card("Код можно использовать только один раз на аккаунт."),
    "Отправьте код сообщением.",
)


class TrialCodeFSM(StatesGroup):
    waiting = State()


def code_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🔑 Код тестовой подписки", callback_data=Nav(to="trialcode").pack())


@router.callback_query(Nav.filter(F.to == "trialcode"))
async def ask_code(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    st = await tc_svc.info(ctx, cb.from_user.id)
    if not st.available:
        await show(cb, ui.screen(ui.title("Код тестовой подписки"), ui.warn(ui.safe(st.reason))), InlineKeyboardMarkup(inline_keyboard=[ui.nav_row()]))
        return
    await state.set_state(TrialCodeFSM.waiting)
    await show(cb, PROMPT, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=Nav(to="menu").pack())]]))


@router.message(StateFilter(TrialCodeFSM.waiting), F.text)
async def got_code(message: Message, ctx: AppContext, state: FSMContext) -> None:
    code = message.text or ""
    try:  # the secret shouldn't stay in the chat history
        await message.delete()
    except Exception:  # noqa: BLE001
        pass
    lock = user_locks.get(message.from_user.id)
    if lock.locked():
        await message.answer(ui.note("Код уже проверяется…"))
        return
    async with lock:
        try:
            res = await tc_svc.activate(ctx, message.from_user.id, code)
        except ServiceError as exc:
            retry = exc.code in ("bad_code", "vpn_failed", "conflict")
            if not retry:
                await state.clear()
            rows = [[InlineKeyboardButton(text="Отмена", callback_data=Nav(to="menu").pack())]] if retry else [ui.nav_row()]
            await message.answer(ui.screen(ui.title("Код тестовой подписки"), ui.warn(ui.safe(exc.message)), ui.note("Можно отправить код ещё раз.") if retry else ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Ссылка и QR-код", callback_data=DevCb(action="link", id=res.device.id).pack())],
        [InlineKeyboardButton(text="Как подключиться", callback_data=Nav(to="howto").pack())],
        ui.nav_row(),
    ])
    await message.answer(ui.screen(ui.title("Код тестовой подписки"), res.message), reply_markup=kb)

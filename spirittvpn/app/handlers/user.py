"""User-facing navigation: start, menu, documents, about, trial, account, promo codes, support."""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app import texts
from app.handlers.buy import show_plan
from app.handlers.common import render
from app.models import ActivationStatus, User
from app.runtime import Deps, notify_admins, send_welcome
from app.services import settings_store
from app.services.formatting import days_word, fmt_money, fmt_remaining
from app.services.ops import create_ticket
from app.services.orders import pending_discount, user_orders
from app.services.promos import activate_promo, describe, user_activations
from app.services.trial import TrialResult, activate_trial
from app.services.users import accept_terms, account_view, get_acceptance, is_active
from app.services.vpn_sync import sync_user_vpn
from app.vpn.base import VPNError
from app.vpn.retry import with_retries

router = Router(name="user")
log = logging.getLogger("spirittvpn.user")

PROMO_MAX_FAILS = 5
PROMO_COOLDOWN = 600


class PromoInput(StatesGroup):
    code = State()


class TicketInput(StatesGroup):
    text = State()


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _main_menu_kb(session: AsyncSession):
    return kb.main_menu(await settings_store.get_int(session, "trial_days"))


# ---------- start / menu / docs / about ----------

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, deps: Deps, user: User, user_created: bool, state: FSMContext):
    await state.clear()
    accepted = await get_acceptance(session, user, deps.settings.legal_docs_version)
    await send_welcome(
        bot, message.chat.id, deps.logo, await settings_store.get_setting(session, "welcome_text"),
        first_run=user_created or accepted is None, trial_days=await settings_store.get_int(session, "trial_days"),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, bot: Bot, session: AsyncSession, deps: Deps, state: FSMContext):
    await state.clear()
    await deps.logo.send(bot, message.chat.id, texts.MAIN_MENU, await _main_menu_kb(session))


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery, session: AsyncSession, deps: Deps, state: FSMContext):
    await state.clear()
    await render(cb, texts.MAIN_MENU, await _main_menu_kb(session), logo=deps.logo)
    await cb.answer()


@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel")
async def cancel(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.clear()
    if isinstance(event, CallbackQuery):
        await render(event, "Действие отменено.\n\n" + texts.MAIN_MENU, await _main_menu_kb(session))
        await event.answer()
    else:
        await event.answer("Действие отменено.", reply_markup=await _main_menu_kb(session))


async def docs_screen(session: AsyncSession, deps: Deps, user: User) -> tuple[str, object]:
    acc = await get_acceptance(session, user, deps.settings.legal_docs_version)
    if acc:
        status = f"\n\n✅ Ты принял условия (версия {escape(acc.version)}) — {deps.fmt_dt(acc.accepted_at)}."
        return texts.DOCS + status, kb.docs()
    return texts.DOCS + "\n\nЕсли согласен с условиями, нажми «✅ Принимаю условия».", kb.docs(accept_next="docs")


@router.message(Command("docs"))
async def cmd_docs(message: Message, session: AsyncSession, deps: Deps, user: User):
    text, markup = await docs_screen(session, deps, user)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "docs")
async def cb_docs(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    text, markup = await docs_screen(session, deps, user)
    await render(cb, text, markup)
    await cb.answer()


@router.callback_query(F.data.startswith("accept:"))
async def cb_accept(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    nxt = cb.data.split(":", 1)[1]
    await accept_terms(session, user, deps.settings.legal_docs_version, now_utc())
    log.info("terms accepted user_id=%s version=%s", user.id, deps.settings.legal_docs_version)
    await cb.answer("✅ Условия приняты")
    if nxt == "trial":
        await run_trial(cb, bot, session, deps, user)
    elif nxt.startswith("plan:"):
        await show_plan(cb, session, deps, user, nxt.split(":", 1)[1])
    else:
        text, markup = await docs_screen(session, deps, user)
        await render(cb, text, markup)


@router.message(Command("about"))
async def cmd_about(message: Message, bot: Bot, deps: Deps):
    await deps.logo.send(bot, message.chat.id, texts.about(), kb.about())


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery, deps: Deps):
    await render(cb, texts.about(), kb.about(), logo=deps.logo)
    await cb.answer()


# ---------- trial ----------

async def run_trial(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User) -> None:
    result, sub = await activate_trial(
        session, user, deps.vpn, deps.settings.legal_docs_version, now_utc(), deps.settings.vpn_retry_attempts
    )
    if result == TrialResult.NEED_TERMS:
        await render(cb, texts.TERMS_REQUIRED.format(action="активировать пробный период"), kb.terms_gate("trial", "menu"))
        return
    if result == TrialResult.ACTIVATED and sub is not None:
        days = await settings_store.get_int(session, "trial_days")
        text = texts.TRIAL_ACTIVATED.format(days=days_word(days), until=deps.fmt_dt(sub.expires_at))
        text += "\n\nНажми «📲 Подключить VPN», чтобы получить настройки и инструкцию."
        if deps.vpn.is_test:
            text += texts.TRIAL_TEST_NOTE
        await render(cb, text, kb.after_trial(True), logo=deps.logo)
        return
    await render(cb, texts.TRIAL_ERRORS[result.value], kb.trial_failed())


@router.callback_query(F.data == "trial")
async def cb_trial(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    await run_trial(cb, bot, session, deps, user)


# ---------- account ----------

STATUS_LABEL = {"active": "🟢 активна", "expired": "🔴 истекла", "none": "⚪️ отсутствует"}


@router.callback_query(F.data == "account")
async def cb_account(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    view = await account_view(session, user, now_utc())
    lines = [f"👤 <b>ТВОЙ АККАУНТ</b>\n{texts.DIV}", f"🆔 Telegram ID: <code>{user.telegram_id}</code>",
             f"🔒 Статус: {STATUS_LABEL[view.status]}"]
    if view.subscription is not None:
        sub = view.subscription
        lines.append(f"📅 Подписка до: {deps.fmt_dt(sub.expires_at)}")
        if view.remaining is not None:
            lines.append(f"⏳ Осталось: {fmt_remaining(view.remaining)}")
        lines.append(f"💳 Тариф: {escape(sub.plan_title or '—')}")
    else:
        lines.append("📅 Подписка до: —")
        lines.append("💳 Тариф: —")
    lines.append(f"🎁 Пробный период: {'использован' if view.trial_used else 'доступен'}")
    if not deps.vpn.is_configured:
        lines.append("\n🛠 <i>VPN-серверы готовятся к запуску — настройки появятся, как только всё заработает.</i>")
    elif deps.vpn.is_test:
        lines.append("\n⚠️ <i>Тестовый режим: VPN-доступ не настоящий.</i>")
    await render(cb, "\n".join(lines), kb.account())
    await cb.answer()


@router.callback_query(F.data == "conn")
async def cb_connections(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    now = now_utc()
    view = await account_view(session, user, now)
    back = kb.back_to("account", "← В аккаунт")
    if view.status != "active" or view.subscription is None:
        await render(cb, "🔑 <b>Мои подключения</b>\n\nАктивной подписки нет. Оформи тариф или активируй пробный период.",
                     kb.expiring())
        return
    if not deps.vpn.is_configured:
        await render(cb, "🛠 <b>Сервис готовится к запуску</b>\n\nVPN-серверы ещё подключаются. "
                         "Как только доступ будет готов, настройки появятся здесь.", back)
        return
    sub = view.subscription
    if sub.vpn_sync_needed and not await sync_user_vpn(session, deps.vpn, user, sub, now, deps.settings.vpn_retry_attempts):
        await render(cb, "⚙️ <b>Доступ настраивается</b>\n\nСервер временно недоступен, мы повторим автоматически. "
                         "Попробуй через несколько минут.", back)
        return
    try:
        conn = await with_retries(lambda: deps.vpn.get_connection(user.telegram_id),
                                  attempts=deps.settings.vpn_retry_attempts, op_name="get_connection")
    except VPNError as exc:
        log.warning("get_connection failed user_id=%s: %s", user.id, type(exc).__name__)
        await render(cb, "⚠️ <b>Не удалось получить конфигурацию</b>\n\nСервер временно недоступен. "
                         "Попробуй позже или напиши в поддержку.", kb.support())
        return
    chat_id = cb.from_user.id
    head = "⚠️ <b>ТЕСТОВЫЕ ДАННЫЕ — НЕ РАБОЧИЙ VPN</b>\n\n" if conn.is_test else ""
    howto = ("\n\n📲 <b>Как подключиться</b>\n1. Установи клиент (например, Hiddify, v2rayNG, Streisand или WireGuard — "
             "в зависимости от протокола сервера).\n2. Импортируй ссылку или файл.\n3. Включи VPN.\n\n"
             "🔐 Не пересылай эти данные другим людям.")
    if conn.link:
        await bot.send_message(chat_id, f"{head}🔑 <b>Твоя ссылка подключения</b>\n\n<code>{escape(conn.link)}</code>{howto}",
                               protect_content=True, reply_markup=back)
    elif conn.config_text:
        await bot.send_document(chat_id, BufferedInputFile(conn.config_text.encode(), conn.config_filename or "spirittvpn.conf"),
                                caption=f"{head}🔑 <b>Твоя конфигурация</b>{howto}", protect_content=True, reply_markup=back)


@router.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    orders = await user_orders(session, user.id, 10)
    if not orders:
        text = "📜 <b>История платежей</b>\n\nПлатежей пока нет."
    else:
        rows = [f"📜 <b>История платежей</b>\n{texts.DIV}"]
        for o in orders:
            test = " 🧪TEST" if o.is_test else ""
            rows.append(f"{texts.ORDER_STATUS.get(o.status, o.status)}{test} · {escape(o.plan_title)} · "
                        f"{fmt_money(o.amount, o.currency)}\n<code>{o.id}</code> · {deps.fmt_dt(o.paid_at or o.created_at)}")
        text = "\n\n".join(rows)
    await render(cb, text, kb.back_to("account", "← В аккаунт"))
    await cb.answer()


@router.callback_query(F.data == "bonuses")
async def cb_bonuses(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    acts = await user_activations(session, user.id)
    pd = await pending_discount(session, user)
    money = lambda v: fmt_money(v, deps.settings.payment_currency)  # noqa: E731
    rows = [f"🎟️ <b>Мои бонусы</b>\n{texts.DIV}"]
    if pd:
        rows.append(f"🎁 Ожидает применения: <b>{escape(pd[1].code)}</b> — {escape(describe(pd[1], money))}")
    status = {ActivationStatus.APPLIED: "✅ применён", ActivationStatus.PENDING: "⏳ ждёт покупки", ActivationStatus.USED: "✅ использован"}
    for act, promo in acts:
        rows.append(f"• <b>{escape(promo.code)}</b> — {escape(describe(promo, money))} · {status.get(act.status, act.status)}")
    if len(rows) == 1:
        rows.append("Бонусов пока нет. Активируй промокод в главном меню.")
    await render(cb, "\n".join(rows), kb.back_to("account", "← В аккаунт"))
    await cb.answer()


# ---------- promo codes ----------

@router.callback_query(F.data == "promo")
async def cb_promo(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("promo_block_until", 0) > time.time():
        await cb.answer("Слишком много попыток. Попробуй через 10 минут.", show_alert=True)
        return
    await state.set_state(PromoInput.code)
    await render(cb, texts.PROMO_PROMPT, kb.cancel())
    await cb.answer()


@router.message(StateFilter(PromoInput.code), F.text)
async def promo_entered(message: Message, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    result = await activate_promo(session, user, message.text or "", now_utc(), vpn_ready=deps.vpn.is_configured)
    if not result.ok:
        data = await state.get_data()
        fails = data.get("promo_fails", 0) + 1
        if fails >= PROMO_MAX_FAILS:
            await state.set_state(None)
            await state.update_data(promo_fails=0, promo_block_until=time.time() + PROMO_COOLDOWN)
            await message.answer("⛔️ Слишком много неудачных попыток. Попробуй через 10 минут.", reply_markup=await _main_menu_kb(session))
            return
        await state.update_data(promo_fails=fails)
        assert result.error is not None
        await message.answer(f"❌ {result.error.value}\n\nПопробуй другой код или нажми «Отмена».", reply_markup=kb.cancel())
        return
    await state.set_state(None)
    await state.update_data(promo_fails=0)
    assert result.promo is not None
    text = texts.promo_applied(describe(result.promo, lambda v: fmt_money(v, deps.settings.payment_currency)))
    markup = kb.expiring()
    if result.subscription is not None:
        ok = await sync_user_vpn(session, deps.vpn, user, result.subscription, now_utc(), deps.settings.vpn_retry_attempts)
        text += f"\n\n📅 Подписка до: <b>{deps.fmt_dt(result.subscription.expires_at)}</b>"
        if not ok:
            text += texts.VPN_PENDING_NOTE
        markup = kb.after_payment()
    else:
        text += "\n\nСкидка применится автоматически при оформлении следующей покупки."
    await message.answer(text, reply_markup=markup)


# ---------- support ----------

@router.message(Command("support"))
async def cmd_support(message: Message, session: AsyncSession):
    await message.answer(texts.SUPPORT, reply_markup=kb.support(await settings_store.get_setting(session, "support_extra_url")))


@router.callback_query(F.data == "support")
async def cb_support(cb: CallbackQuery, session: AsyncSession, state: FSMContext):
    await state.set_state(None)
    await render(cb, texts.SUPPORT, kb.support(await settings_store.get_setting(session, "support_extra_url")))
    await cb.answer()


@router.callback_query(F.data.startswith("faq:"))
async def cb_faq(cb: CallbackQuery):
    key = cb.data.split(":", 1)[1]
    await render(cb, texts.FAQ.get(key, texts.FAQ["general"]), kb.back_to("support", "← В поддержку"))
    await cb.answer()


@router.callback_query(F.data == "ticket")
async def cb_ticket(cb: CallbackQuery, state: FSMContext):
    await state.set_state(TicketInput.text)
    await render(cb, texts.TICKET_PROMPT, kb.cancel())
    await cb.answer()


@router.message(StateFilter(TicketInput.text), F.text)
async def ticket_text(message: Message, bot: Bot, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Опиши проблему чуть подробнее 🙂", reply_markup=kb.cancel())
        return
    tg_id, username = user.telegram_id, user.username
    ticket = await create_ticket(session, user, "general", text)
    await state.set_state(None)
    await message.answer(texts.TICKET_CREATED.format(id=ticket.id), reply_markup=await _main_menu_kb(session))
    who = f"<code>{tg_id}</code>" + (f" (@{escape(username)})" if username else "")
    await notify_admins(
        bot, deps.settings,
        f"🎫 <b>Обращение #{ticket.id}</b>\nОт: {who}\n\n{escape(text[:3000])}\n\nОтветить: <code>/reply {ticket.id} текст</code>",
    )


@router.message(StateFilter(PromoInput.code, TicketInput.text))
async def wrong_input(message: Message):
    await message.answer("Отправь, пожалуйста, текстовое сообщение или нажми «Отмена».", reply_markup=kb.cancel())


@router.callback_query()
async def unknown_callback(cb: CallbackQuery):
    await cb.answer("Кнопка устарела. Открой /menu", show_alert=False)


@router.message(StateFilter(None), F.text)
async def fallback_text(message: Message, session: AsyncSession):
    await message.answer("Выбери действие в меню 👇", reply_markup=await _main_menu_kb(session))


async def _unused(_: TelegramAPIError) -> None:  # pragma: no cover
    pass

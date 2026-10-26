"""Bot UI: welcome, fallback menu on plain messages (account, subscription, devices, referrals), support and docs."""
from __future__ import annotations

import io
import logging
import time
from datetime import UTC, datetime
from html import escape

import segno
from aiogram import Bot, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app import texts
from app.handlers.buy import show_plan
from app.handlers.common import RouterTemplate, render
from app.models import ActivationStatus, DeviceStatus, User
from app.runtime import Deps, notify_admins, notify_referral, send_welcome
from app.services import devices as dev
from app.services import referrals as refs
from app.services import settings_store
from app.services.formatting import fmt_money, fmt_remaining
from app.services.ops import TICKET_CATEGORIES, create_ticket
from app.services.orders import pending_discount, user_orders
from app.services.promos import activate_promo, describe, user_activations
from app.services.trial import TrialResult, activate_trial
from app.services.users import accept_terms, account_view, get_acceptance, live_devices
from app.services.devices import sync_user_vpn

router = RouterTemplate("user")
log = logging.getLogger("spirittvpn.user")

PROMO_MAX_FAILS = 5
PROMO_COOLDOWN = 600


class PromoInput(StatesGroup):
    code = State()


class TicketInput(StatesGroup):
    text = State()


class DeviceInput(StatesGroup):
    name = State()
    rename = State()


def now_utc() -> datetime:
    return datetime.now(UTC)


# ---------- start / home / docs / about ----------

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, session: AsyncSession, deps: Deps, user: User, user_created: bool,
                    state: FSMContext):
    await state.clear()
    accepted = await get_acceptance(session, user, deps.settings.legal_docs_version)
    await send_welcome(
        bot, message.chat.id, deps.logo, await settings_store.get_setting(session, "welcome_text"),
        first_run=user_created or accepted is None, webapp_url=deps.webapp_url,
        invited=user_created and user.referrer_id is not None,
    )


async def home_text(session: AsyncSession, deps: Deps, user: User) -> str:
    view = await account_view(session, user, now_utc())
    lines = [f"<b>SPIRITTVPN</b>\n{texts.DIV}"]
    if not deps.vpn.is_configured:
        lines.append("🛠 VPN-серверы готовятся к запуску. Подключение станет доступно после настройки инфраструктуры.")
    elif view.status in ("active", "soon", "pending"):
        sub = view.subscription
        assert sub is not None
        lines.append(f"🟢 <b>VPN доступен</b> · {escape(sub.plan_title or '—')}")
        lines.append(f"📅 {'Бессрочная' if sub.is_lifetime else 'до ' + deps.fmt_dt(sub.expires_at)}")
        lines.append(f"📱 Устройства: {view.devices_used} из {view.device_limit}")
    elif view.status == "suspended":
        lines.append("⏸ Подписка временно отключена. Напиши в поддержку.")
    else:
        lines.append("⚪️ <b>Подписка не активна</b>\nВыбери тариф или активируй пробный период в разделе «Подписка».")
    if deps.vpn.is_test:
        lines.append(texts.TEST_NOTE.strip())
    if await settings_store.get_bool(session, "maintenance"):
        lines.append("\n🛠 Идут технические работы.")
    return "\n".join(lines)


@router.message(Command("menu"))
async def cmd_menu(message: Message, bot: Bot, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    await state.clear()
    await deps.logo.send(bot, message.chat.id, await home_text(session, deps, user), kb.home(deps.webapp_url))


@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    await state.clear()
    await render(cb, await home_text(session, deps, user), kb.home(deps.webapp_url), logo=deps.logo)
    await cb.answer()


@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel")
async def cancel(event: Message | CallbackQuery, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    await state.clear()
    text = "Действие отменено.\n\n" + await home_text(session, deps, user)
    if isinstance(event, CallbackQuery):
        await render(event, text, kb.home(deps.webapp_url))
        await event.answer()
    else:
        await event.answer(text, reply_markup=kb.home(deps.webapp_url))


async def docs_screen(session: AsyncSession, deps: Deps, user: User) -> tuple[str, object]:
    acc = await get_acceptance(session, user, deps.settings.legal_docs_version)
    if acc:
        status = f"\n\n✅ Условия приняты (версия {escape(acc.version)}) — {deps.fmt_dt(acc.accepted_at)}."
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


# ---------- account ----------

async def account_text(session: AsyncSession, deps: Deps, user: User) -> str:
    view = await account_view(session, user, now_utc())
    sub = view.subscription
    lines = [f"👤 <b>ТВОЙ АККАУНТ</b>\n{texts.DIV}", f"🆔 Telegram ID: <code>{user.telegram_id}</code>",
             f"🔒 Статус: {texts.SUB_STATUS[view.status]}"]
    if sub is not None:
        lines.append(f"🗓 Начало: {deps.fmt_dt(sub.starts_at)}")
        lines.append(f"📅 Подписка до: {deps.fmt_until(sub)}")
        if view.remaining is not None:
            lines.append(f"⏳ Осталось: {fmt_remaining(view.remaining)}")
        lines.append(f"💳 Тариф: {escape(sub.plan_title or '—')}")
        lines.append(f"📱 Устройства: {view.devices_used} из {view.device_limit}")
    else:
        lines += ["📅 Подписка до: —", "💳 Тариф: —"]
    lines.append(f"🎁 Пробный период: {'использован' if view.trial_used else 'доступен'}")
    if not deps.vpn.is_configured:
        lines.append("\n🛠 VPN-серверы готовятся к запуску — настройки появятся, как только всё заработает.")
    elif deps.vpn.is_test:
        lines.append(texts.TEST_NOTE)
    return "\n".join(lines)


@router.callback_query(F.data == "account")
async def cb_account(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    await render(cb, await account_text(session, deps, user), kb.account(user.notifications_enabled))
    await cb.answer()


@router.callback_query(F.data == "notif")
async def cb_notif(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    user.notifications_enabled = not user.notifications_enabled
    await session.commit()
    await render(cb, await account_text(session, deps, user), kb.account(user.notifications_enabled))
    await cb.answer("Уведомления " + ("включены" if user.notifications_enabled else "выключены"))


@router.callback_query(F.data == "history")
async def cb_history(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    orders = await user_orders(session, user.id, 10)
    if not orders:
        text = "📜 <b>Платежи</b>\n\nПлатежей пока нет."
    else:
        rows = [f"📜 <b>Платежи</b>\n{texts.DIV}"]
        for o in orders:
            test = " 🧪TEST" if o.is_test else ""
            rows.append(f"{texts.ORDER_STATUS.get(o.status, o.status)}{test} · {escape(o.plan_title)} · "
                        f"{fmt_money(o.amount, o.currency)}\n<code>{o.id}</code> · {deps.fmt_dt(o.paid_at or o.created_at)}")
        text = "\n\n".join(rows)
    await render(cb, text, kb.back_to("account", "← В аккаунт"))
    await cb.answer()


@router.callback_query(F.data == "bonuses")
async def cb_bonuses(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    entries = await refs.bonus_history(session, user.id, 20)
    acts = await user_activations(session, user.id)
    pd = await pending_discount(session, user)
    money = lambda v: fmt_money(v, deps.settings.payment_currency)  # noqa: E731
    rows = [f"🎁 <b>Бонусы</b>\n{texts.DIV}"]
    if pd:
        rows.append(f"⏳ Ждёт покупки: <b>{escape(pd[1].code)}</b> — {escape(describe(pd[1], money))}")
    for e in entries:
        rows.append(f"{'+' if e.days > 0 else ''}{e.days} дн. · {escape(e.description)} · {deps.fmt_dt(e.created_at)}")
    for act, promo in acts:
        if act.status != ActivationStatus.APPLIED:
            rows.append(f"🎟 {escape(promo.code)} — {escape(describe(promo, money))} · "
                        f"{'использован' if act.status == ActivationStatus.USED else 'ждёт покупки'}")
    if len(rows) == 1:
        rows.append("Бонусов пока нет. Приглашай друзей или активируй промокод.")
    await render(cb, "\n".join(rows), kb.back_to("account", "← В аккаунт"))
    await cb.answer()


# ---------- subscription / trial / promo ----------

@router.callback_query(F.data == "sub")
async def cb_sub(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    view = await account_view(session, user, now_utc())
    text = await account_text(session, deps, user)
    trial_ok = (not view.trial_used and view.subscription is None and deps.vpn.is_configured
                and await settings_store.get_bool(session, "trial_enabled"))
    await render(cb, text, kb.subscription(trial_ok, view.status in ("active", "soon", "pending")))
    await cb.answer()


async def run_trial(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User) -> None:
    out = await activate_trial(session, user, deps.vpn, deps.settings.legal_docs_version, now_utc(),
                               device_limit=deps.settings.trial_device_limit, prefix=deps.settings.vpn_username_prefix)
    if out.result == TrialResult.NEED_TERMS:
        await render(cb, texts.TERMS_REQUIRED.format(action="активировать пробный период"), kb.terms_gate("trial", "sub"))
        return
    if out.result == TrialResult.ACTIVATED and out.subscription is not None:
        text = texts.TRIAL_ACTIVATED.format(until=deps.fmt_dt(out.subscription.expires_at))
        if deps.vpn.is_test:
            text += texts.TEST_NOTE
        await render(cb, text, kb.after_trial(), logo=deps.logo)
        if out.reward:
            await notify_referral(bot, out.reward)
        return
    await render(cb, texts.TRIAL_ERRORS[out.result.value], kb.trial_failed())


@router.callback_query(F.data == "trial")
async def cb_trial(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    await run_trial(cb, bot, session, deps, user)


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
            await message.answer("⛔️ Слишком много неудачных попыток. Попробуй через 10 минут.", reply_markup=kb.home(deps.webapp_url))
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
        ok = await sync_user_vpn(session, deps.vpn, user, result.subscription, now_utc())
        text += f"\n\n📅 Подписка до: <b>{deps.fmt_until(result.subscription)}</b>"
        if not ok:
            text += texts.VPN_PENDING_NOTE
        markup = kb.after_payment()
    else:
        text += "\n\nСкидка применится автоматически при оформлении следующей покупки."
    await message.answer(text, reply_markup=markup)


# ---------- devices ----------

async def devices_screen(session: AsyncSession, deps: Deps, user: User) -> tuple[str, object]:
    view = await account_view(session, user, now_utc())
    items = await live_devices(session, user.id)
    lines = [f"📱 <b>Мои устройства</b>\n{texts.DIV}"]
    if not deps.vpn.is_configured:
        lines.append(texts.SERVICE_NOT_READY)
        return "\n".join(lines), kb.back_to("home")
    active = view.status in ("active", "soon", "pending")
    lines.append(f"Использовано {view.devices_used} из {view.device_limit} устройств" if view.subscription else
                 "Нет активной подписки.")
    if not active:
        lines.append("Оформи тариф или активируй пробный период, чтобы подключить устройства.")
    lines.append("<i>Каждое устройство получает собственную ссылку. Не передавай её другим людям.</i>")
    icon = {DeviceStatus.ACTIVE: "🟢", DeviceStatus.SUSPENDED: "⏸", DeviceStatus.PENDING: "⚙️"}
    buttons = [(d.public_id, f"{icon.get(d.status, '•')} {d.name}") for d in items]
    return "\n".join(lines), kb.devices(buttons, active and view.devices_used < view.device_limit)


@router.callback_query(F.data == "devices")
async def cb_devices(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    await state.set_state(None)
    text, markup = await devices_screen(session, deps, user)
    await render(cb, text, markup)
    await cb.answer()


@router.callback_query(F.data == "dv:add")
async def cb_device_add(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    default = f"Устройство {len(await live_devices(session, user.id)) + 1}"
    await state.set_state(DeviceInput.name)
    await state.update_data(default_name=default)
    await render(cb, texts.DEVICE_NAME_PROMPT, kb.device_name_quick(default))
    await cb.answer()


async def _finish_add(target: Message, session: AsyncSession, deps: Deps, user: User, name: str) -> None:
    res = await dev.add_device(session, deps.vpn, user, name, now_utc(), deps.settings.vpn_username_prefix)
    if not res.ok:
        assert res.error is not None
        await target.answer(f"⚠️ {res.error.value}", reply_markup=kb.back_to("devices", "← К устройствам"))
        return
    assert res.device is not None
    note = texts.TEST_NOTE if deps.vpn.is_test else ""
    await target.answer(f"✅ Устройство «{escape(res.device.name)}» добавлено.{note}",
                        reply_markup=kb.device(res.device.public_id, True))


@router.callback_query(F.data == "dv:quick", StateFilter(DeviceInput.name))
async def cb_device_quick(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    name = (await state.get_data()).get("default_name", "Устройство")
    await state.set_state(None)
    await cb.answer()
    assert isinstance(cb.message, Message)
    await _finish_add(cb.message, session, deps, user, name)


@router.message(StateFilter(DeviceInput.name), F.text)
async def device_name_entered(message: Message, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    await state.set_state(None)
    await _finish_add(message, session, deps, user, message.text or "")


@router.callback_query(F.data.startswith("dv:o:"))
async def cb_device_open(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    device = await dev.get_owned_device(session, user.id, cb.data[5:])
    if device is None:
        await cb.answer("Устройство не найдено", show_alert=True)
        return
    view = await account_view(session, user, now_utc())
    status = {DeviceStatus.ACTIVE: "🟢 активно", DeviceStatus.SUSPENDED: "⏸ приостановлено",
              DeviceStatus.PENDING: "⚙️ настраивается"}.get(device.status, device.status)
    until = "Бессрочно" if view.is_lifetime else (deps.fmt_dt(view.subscription.expires_at) if view.subscription else "—")
    text = (f"📱 <b>{escape(device.name)}</b>\n{texts.DIV}\nСтатус: {status}\nДобавлено: {deps.fmt_dt(device.created_at)}\n"
            f"Действует до: {until}\nID: <code>{device.public_id[:8]}</code>")
    await render(cb, text, kb.device(device.public_id, device.status == DeviceStatus.ACTIVE))
    await cb.answer()


@router.callback_query(F.data.startswith("dv:c:"))
async def cb_device_conn(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps, user: User):
    await cb.answer()
    res = await dev.device_connection(session, deps.vpn, user, cb.data[5:])
    if not res.ok or res.connection is None:
        assert res.error is not None
        await bot.send_message(cb.from_user.id, f"⚠️ {res.error.value}", reply_markup=kb.back_to("devices", "← К устройствам"))
        return
    link = res.connection.link
    head = "⚠️ <b>ТЕСТОВЫЕ ДАННЫЕ — НЕ РАБОЧИЙ VPN</b>\n\n" if res.connection.is_test else ""
    howto = ("\n\n📲 <b>Как подключиться</b>\n1. Установи клиент: Hiddify, v2RayTun, Streisand (iOS) или v2rayNG (Android).\n"
             "2. Импортируй ссылку или отсканируй QR-код.\n3. Включи VPN.\n\n🔐 Не пересылай ссылку другим людям.")
    png = io.BytesIO()
    segno.make(link, error="m").save(png, kind="png", scale=8, border=2, dark="#0b0b14", light="#ffffff")
    await bot.send_photo(cb.from_user.id, BufferedInputFile(png.getvalue(), "spirittvpn-qr.png"),
                         caption=f"{head}🔗 <b>{escape(res.device.name if res.device else '')}</b>\n\n<code>{escape(link)}</code>{howto}",
                         protect_content=True, reply_markup=kb.back_to("devices", "← К устройствам"))


@router.callback_query(F.data.startswith("dv:n:"))
async def cb_device_rename(cb: CallbackQuery, state: FSMContext):
    await state.set_state(DeviceInput.rename)
    await state.update_data(device=cb.data[5:])
    await render(cb, "✏️ Отправь новое название (до 32 символов).", kb.cancel())
    await cb.answer()


@router.message(StateFilter(DeviceInput.rename), F.text)
async def device_renamed(message: Message, session: AsyncSession, user: User, state: FSMContext):
    pid = (await state.get_data()).get("device", "")
    await state.set_state(None)
    res = await dev.rename_device(session, user, pid, message.text or "")
    if not res.ok:
        assert res.error is not None
        await message.answer(f"⚠️ {res.error.value}", reply_markup=kb.back_to("devices"))
        return
    assert res.device is not None
    await message.answer(f"✅ Переименовано в «{escape(res.device.name)}».", reply_markup=kb.device(pid, res.device.status == DeviceStatus.ACTIVE))


@router.callback_query(F.data.startswith("dv:x:") | F.data.startswith("dv:r:"))
async def cb_device_confirm(cb: CallbackQuery, session: AsyncSession, user: User):
    action, pid = cb.data[3], cb.data[5:]
    device = await dev.get_owned_device(session, user.id, pid)
    if device is None:
        await cb.answer("Устройство не найдено", show_alert=True)
        return
    if action == "x":
        text = f"🗑 Отозвать доступ для «{escape(device.name)}»?\n\nСсылка перестанет работать, место освободится."
        markup = kb.confirm(f"dv:X:{pid}", f"dv:o:{pid}", "🗑 Да, отозвать")
    else:
        text = f"🔁 Заменить «{escape(device.name)}»?\n\nСтарая ссылка перестанет работать, будет создана новая."
        markup = kb.confirm(f"dv:R:{pid}", f"dv:o:{pid}", "🔁 Да, заменить")
    await render(cb, text, markup)
    await cb.answer()


@router.callback_query(F.data.startswith("dv:X:") | F.data.startswith("dv:R:"))
async def cb_device_do(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    action, pid = cb.data[3], cb.data[5:]
    if action == "X":
        res = await dev.revoke_device(session, deps.vpn, user, pid, now_utc())
        ok_text = "✅ Доступ отозван, устройство удалено."
    else:
        res = await dev.replace_device(session, deps.vpn, user, pid, None, now_utc(), deps.settings.vpn_username_prefix)
        ok_text = "✅ Устройство заменено. Получи новую ссылку подключения."
    if not res.ok:
        assert res.error is not None
        await render(cb, f"⚠️ {res.error.value}", kb.back_to("devices", "← К устройствам"))
    else:
        text, markup = await devices_screen(session, deps, user)
        await render(cb, f"{ok_text}\n\n{text}", markup)
    await cb.answer()


# ---------- referrals ----------

@router.callback_query(F.data == "refs")
async def cb_refs(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    s = await refs.summary(session, user)
    link = refs.referral_link(deps.bot_username, user.telegram_id)
    enabled = await settings_store.get_bool(session, "referral_enabled")
    trial_days = await settings_store.get_int(session, "referral_trial_days")
    pay_days = await settings_store.get_int(session, "referral_payment_days")
    lines = [f"👥 <b>Реферальная программа SpirittVPN</b>\n{texts.DIV}", "Приглашай друзей и получай бонусные дни VPN!"]
    if enabled:
        lines.append(f"\n🎁 +{trial_days} дн. — когда друг активирует пробный период\n"
                     f"💳 +{pay_days} дн. — после его первой оплаты")
    else:
        lines.append("\n⏸ Программа временно приостановлена — новые бонусы не начисляются.")
    lines.append(f"\n🔗 Твоя ссылка: <code>{escape(link)}</code>" if link else "\n🔗 Ссылка временно недоступна.")
    lines += [f"👤 Приглашено друзей: {s.invited}", f"🎁 Получено бонусных дней: {s.bonus_days}",
              f"⏳ Ожидают выполнения условий: {s.pending}"]
    await render(cb, "\n".join(lines), kb.referrals(refs.share_url(link) if link else None))
    await cb.answer()


@router.callback_query(F.data == "refs:inv")
async def cb_invites(cb: CallbackQuery, session: AsyncSession, deps: Deps, user: User):
    items = await refs.invites(session, user, 30)
    rows = [f"📊 <b>Мои приглашения</b>\n{texts.DIV}"]
    for i in items:
        rows.append(f"{i.name} · {deps.fmt_dt(i.invited_at)}\n"
                    f"{'✅' if i.trial_done else '⏳'} пробный период · {'✅' if i.payment_done else '⏳'} первая оплата"
                    f" · +{i.bonus_days} дн.")
    if len(rows) == 1:
        rows.append("Пока никого. Поделись ссылкой с друзьями!")
    await render(cb, "\n\n".join(rows), kb.back_to("refs"))
    await cb.answer()


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
async def cb_ticket(cb: CallbackQuery):
    await render(cb, texts.TICKET_CATEGORY_PROMPT, kb.ticket_categories())
    await cb.answer()


@router.callback_query(F.data.startswith("tcat:"))
async def cb_ticket_cat(cb: CallbackQuery, state: FSMContext):
    await state.set_state(TicketInput.text)
    await state.update_data(ticket_cat=cb.data.split(":", 1)[1])
    await render(cb, texts.TICKET_PROMPT, kb.cancel())
    await cb.answer()


@router.message(StateFilter(TicketInput.text), F.text)
async def ticket_text(message: Message, bot: Bot, session: AsyncSession, deps: Deps, user: User, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Опиши проблему чуть подробнее 🙂", reply_markup=kb.cancel())
        return
    category = (await state.get_data()).get("ticket_cat", "general")
    tg_id, username = user.telegram_id, user.username
    ticket = await create_ticket(session, user, category, text)
    await state.set_state(None)
    await message.answer(texts.TICKET_CREATED.format(id=ticket.id), reply_markup=kb.home(deps.webapp_url))
    who = f"<code>{tg_id}</code>" + (f" (@{escape(username)})" if username else "")
    await notify_admins(
        bot, deps.settings,
        f"🎫 <b>Обращение #{ticket.id}</b> · {TICKET_CATEGORIES.get(ticket.topic, ticket.topic)}\nОт: {who}\n\n"
        f"{escape(text[:3000])}\n\nОтветить: <code>/reply {ticket.id} текст</code>",
    )


@router.message(StateFilter(PromoInput.code, TicketInput.text, DeviceInput.name, DeviceInput.rename))
async def wrong_input(message: Message):
    await message.answer("Отправь, пожалуйста, текстовое сообщение или нажми «Отмена».", reply_markup=kb.cancel())


@router.callback_query()
async def unknown_callback(cb: CallbackQuery):
    await cb.answer("Кнопка устарела. Открой /menu")


@router.message(StateFilter(None), F.text)
async def fallback_text(message: Message, deps: Deps):
    await message.answer("Выбери действие 👇", reply_markup=kb.start(deps.webapp_url))

"""Admin panel. Router-level filter + per-service require_admin() — two independent server-side checks."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app import keyboards as kb
from app import texts
from app.handlers.common import render
from app.models import Broadcast, Order
from app.payments.providers import PaymentEventData, TestPaymentProvider
from app.runtime import Deps, deliver_payment_result, make_notifier
from app.services import admin as svc
from app.services.formatting import fmt_money
from app.services.ops import add_admin_reply, close_ticket, open_tickets, run_broadcast
from app.services.orders import process_payment_event
from app.services.settings_store import all_settings
from app.services.users import get_user

log = logging.getLogger("spirittvpn.admin")

ADMIN_COMMANDS = (
    "admin", "stats", "user", "extend", "block", "unblock", "group", "plans", "plan_add", "plan_set", "promos",
    "promo", "promo_add", "promo_set", "promo_on", "promo_off", "promo_archive", "settings", "set", "broadcast",
    "tickets", "reply", "close", "audit", "order", "testpay", "confirm_payment",
)


class BroadcastInput(StatesGroup):
    text = State()


def build_admin_router(deps: Deps) -> Router:
    router = Router(name="admin")
    is_admin = F.from_user.id.in_(deps.settings.admin_ids)
    router.message.filter(is_admin)
    router.callback_query.filter(is_admin)
    _register(router)
    return router


def build_denied_router() -> Router:
    """Catches admin entry points from non-admins: logs and refuses without revealing anything."""
    router = Router(name="admin_denied")

    @router.message(Command(*ADMIN_COMMANDS))
    async def denied_cmd(message: Message):
        log.warning("non-admin tg=%s tried admin command", message.from_user.id if message.from_user else None)
        await message.answer(texts.ADMIN_DENIED)

    @router.callback_query(F.data.startswith("adm:"))
    async def denied_cb(cb: CallbackQuery):
        log.warning("non-admin tg=%s tried admin callback", cb.from_user.id)
        await cb.answer(texts.ADMIN_DENIED, show_alert=True)

    return router


def now_utc() -> datetime:
    return datetime.now(UTC)


def _args(command: CommandObject) -> list[str]:
    return (command.args or "").split()


async def _guard(message: Message, coro):
    try:
        return await coro
    except svc.AdminInputError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}")
    except svc.AdminPermissionError:
        await message.answer(texts.ADMIN_DENIED)
    return None


HELP = {
    "users": (
        "👥 <b>Пользователи</b>\n\n"
        "<code>/user ID</code> — карточка, платежи, промокоды\n"
        "<code>/extend ID ДНИ причина</code> — ручное продление (в журнал)\n"
        "<code>/block ID</code> · <code>/unblock ID</code>\n"
        "<code>/group ID имя|-</code> — группа для спец-промокодов"
    ),
    "plans": (
        "💳 <b>Тарифы</b>\n\n<code>/plans</code> — список\n"
        "<code>/plan_add код дни цена Название</code> (цена в минимальных единицах; создаётся выключенным)\n"
        "<code>/plan_set код price=29900 days=30 title=1_месяц desc=Текст active=on sort=1</code>"
    ),
    "promos": (
        "🎟️ <b>Промокоды</b>\n\n<code>/promos</code> — список · <code>/promo КОД</code> — статистика\n"
        "<code>/promo_add КОД days|percent|fixed ЗНАЧЕНИЕ [max=100] [until=2026-12-31] [from=2026-11-01] "
        "[group=vip] [new=on] [desc=Описание_через_подчёркивания]</code>\n"
        "<code>/promo_set КОД поле=значение ...</code>\n"
        "<code>/promo_on КОД</code> · <code>/promo_off КОД</code> · <code>/promo_archive КОД</code>"
    ),
    "broadcast": "📢 <b>Рассылка</b>\n\n<code>/broadcast</code> — отправь текст (HTML), бот покажет предпросмотр и попросит подтверждение.",
}


def _register(router: Router) -> None:
    @router.message(Command("admin"))
    async def admin_menu(message: Message):
        await message.answer("🛡 <b>Панель администратора SpirittVPN</b>", reply_markup=kb.admin_menu())

    @router.callback_query(F.data == "adm:menu")
    async def admin_menu_cb(cb: CallbackQuery):
        await render(cb, "🛡 <b>Панель администратора SpirittVPN</b>", kb.admin_menu())
        await cb.answer()

    async def stats_text(session: AsyncSession, deps: Deps, admin_id: int) -> str:
        s = await svc.stats(session, deps.settings, admin_id, now_utc())
        revenue = ", ".join(fmt_money(v, c) for c, v in s.revenue.items()) or "0"
        return (
            f"📊 <b>Статистика</b>\n{texts.DIV}\n"
            f"👥 Пользователей: {s.users_total} (за сутки +{s.users_day}, за 30 дней +{s.users_month})\n"
            f"🟢 Активных подписок: {s.active_subs}\n🎁 Пробных периодов: {s.trials_used}\n"
            f"🧾 Оплаченных заказов: {s.paid_orders}\n💰 Выручка (подтверждённая, без тестовых): {revenue}\n"
            f"🎟️ Действующих промокодов: {s.active_promos}"
        )

    @router.message(Command("stats"))
    async def stats_cmd(message: Message, session: AsyncSession, deps: Deps):
        await message.answer(await stats_text(session, deps, message.from_user.id), reply_markup=kb.admin_back())

    @router.callback_query(F.data == "adm:stats")
    async def stats_cb(cb: CallbackQuery, session: AsyncSession, deps: Deps):
        await render(cb, await stats_text(session, deps, cb.from_user.id), kb.admin_back())
        await cb.answer()

    @router.callback_query(F.data.in_({"adm:users", "adm:plans", "adm:promos"}))
    async def section_cb(cb: CallbackQuery, session: AsyncSession, deps: Deps):
        key = cb.data.split(":")[1]
        text = HELP[key]
        if key == "plans":
            text += "\n\n" + await plans_text(session, deps, cb.from_user.id)
        elif key == "promos":
            text += "\n\n" + await promos_text(session, deps, cb.from_user.id)
        await render(cb, text, kb.admin_back())
        await cb.answer()

    # ----- users -----
    async def user_card_text(session: AsyncSession, deps: Deps, admin_id: int, tg_id: int):
        card = await svc.user_card(session, deps.settings, admin_id, tg_id)
        if card is None:
            return None, None
        u, sub = card.user, card.subscription
        lines = [f"👤 <b>Пользователь</b> <code>{u.telegram_id}</code>" + (f" @{escape(u.username)}" if u.username else ""),
                 f"Создан: {deps.fmt_dt(u.created_at)} · Группа: {escape(u.group or '—')}",
                 f"Блокировка: {'⛔️ да' if u.is_blocked else 'нет'} · Бот заблокирован юзером: {'да' if u.bot_blocked else 'нет'}",
                 f"Пробный: {'использован' if card.trial_used else 'нет'}"]
        if sub:
            lines.append(f"Подписка: {sub.status} · {escape(sub.plan_title or '—')} · до {deps.fmt_dt(sub.expires_at)}"
                         f"{' · ⚙️ VPN sync pending' if sub.vpn_sync_needed else ''}")
        else:
            lines.append("Подписка: нет")
        if card.orders:
            lines.append("\n🧾 <b>Заказы:</b>")
            lines += [f"• {o.status}{' TEST' if o.is_test else ''} {escape(o.plan_title)} {fmt_money(o.amount, o.currency)} "
                      f"<code>{o.id}</code>" for o in card.orders]
        if card.activations:
            lines.append("\n🎟️ <b>Промокоды:</b>")
            lines += [f"• {escape(p.code)} ({p.kind} {p.value}) — {a.status}" for a, p in card.activations]
        return "\n".join(lines), kb.admin_user(u.telegram_id, u.is_blocked)

    @router.message(Command("user"))
    async def user_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) != 1 or not args[0].isdigit():
            await message.answer(HELP["users"])
            return
        text, markup = await user_card_text(session, deps, message.from_user.id, int(args[0]))
        await message.answer(text or "Пользователь не найден.", reply_markup=markup)

    @router.message(Command("extend"))
    async def extend_cmd(message: Message, command: CommandObject, bot: Bot, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) < 3 or not args[0].isdigit() or not args[1].isdigit():
            await message.answer("Формат: <code>/extend ID ДНИ причина</code>")
            return
        await _do_extend(message, bot, session, deps, int(args[0]), int(args[1]), " ".join(args[2:]))

    async def _do_extend(message: Message, bot: Bot, session: AsyncSession, deps: Deps, tg_id: int, days: int, reason: str):
        sub = await _guard(message, svc.manual_extend(session, deps.settings, message.chat.id, tg_id, days, reason, now_utc()))
        if sub is None:
            return
        user = await get_user(session, tg_id)
        from app.services.vpn_sync import sync_user_vpn

        ok = user is not None and await sync_user_vpn(session, deps.vpn, user, sub, now_utc(), deps.settings.vpn_retry_attempts)
        await message.answer(f"✅ Продлено на {days} дн. до {deps.fmt_dt(sub.expires_at)}. VPN: {'синхронизирован' if ok else 'в очереди'}")
        await make_notifier(bot)(tg_id, f"🎁 Администратор продлил твою подписку SpirittVPN.\n📅 До: <b>{deps.fmt_dt(sub.expires_at)}</b>", None)

    @router.callback_query(F.data.startswith("adm:ext:"))
    async def extend_cb(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps):
        _, _, tg, days = cb.data.split(":")
        await cb.answer()
        assert isinstance(cb.message, Message)
        await _do_extend(cb.message, bot, session, deps, int(tg), int(days), "кнопка в карточке пользователя")

    @router.message(Command("block", "unblock"))
    async def block_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) != 1 or not args[0].isdigit():
            await message.answer("Формат: <code>/block ID</code>")
            return
        user = await _guard(message, svc.set_blocked(session, deps.settings, message.from_user.id, int(args[0]), command.command == "block"))
        if user:
            await message.answer(f"Готово: {'заблокирован' if user.is_blocked else 'разблокирован'} <code>{user.telegram_id}</code>")

    @router.callback_query(F.data.startswith("adm:blk:"))
    async def block_cb(cb: CallbackQuery, session: AsyncSession, deps: Deps):
        _, _, tg, flag = cb.data.split(":")
        try:
            await svc.set_blocked(session, deps.settings, cb.from_user.id, int(tg), flag == "1")
        except svc.AdminInputError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        text, markup = await user_card_text(session, deps, cb.from_user.id, int(tg))
        await render(cb, text or "—", markup)
        await cb.answer("Готово")

    @router.message(Command("group"))
    async def group_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) != 2 or not args[0].isdigit():
            await message.answer("Формат: <code>/group ID имя</code> (или <code>-</code> чтобы убрать)")
            return
        user = await _guard(message, svc.set_user_group(session, deps.settings, message.from_user.id, int(args[0]),
                                                        None if args[1] == "-" else args[1]))
        if user:
            await message.answer(f"Группа: {escape(user.group or '—')}")

    # ----- plans -----
    async def plans_text(session: AsyncSession, deps: Deps, admin_id: int) -> str:
        plans = await svc.list_plans(session, deps.settings, admin_id)
        rows = [f"{'🟢' if p.is_active else '⚪️'} <code>{p.code}</code> · {escape(p.title)} · {p.duration_days} дн. · "
                f"{fmt_money(p.price, p.currency) if p.price else 'цена не задана'}" for p in plans]
        return "\n".join(rows) or "Тарифов нет."

    @router.message(Command("plans"))
    async def plans_cmd(message: Message, session: AsyncSession, deps: Deps):
        await message.answer(HELP["plans"] + "\n\n" + await plans_text(session, deps, message.from_user.id))

    @router.message(Command("plan_add"))
    async def plan_add(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) < 4 or not args[1].isdigit() or not args[2].isdigit():
            await message.answer(HELP["plans"])
            return
        plan = await _guard(message, svc.create_plan(session, deps.settings, message.from_user.id, args[0], int(args[1]),
                                                     int(args[2]), " ".join(args[3:])))
        if plan:
            await message.answer(f"✅ Тариф <code>{plan.code}</code> создан (выключен). Включить: <code>/plan_set {plan.code} active=on</code>")

    @router.message(Command("plan_set"))
    async def plan_set(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) < 2:
            await message.answer(HELP["plans"])
            return
        plan = await _guard(message, svc.update_plan(session, deps.settings, message.from_user.id, args[0], args[1:]))
        if plan:
            await message.answer(f"✅ Обновлено:\n" + await plans_text(session, deps, message.from_user.id))

    # ----- promos -----
    async def promos_text(session: AsyncSession, deps: Deps, admin_id: int) -> str:
        promos = await svc.list_promos(session, deps.settings, admin_id)
        rows = []
        for p in promos:
            limit = f"{p.activations_count}/{p.max_activations}" if p.max_activations else f"{p.activations_count}/∞"
            until = f" до {p.valid_until:%Y-%m-%d}" if p.valid_until else ""
            extra = (f" grp={p.allowed_group}" if p.allowed_group else "") + (" new" if p.new_users_only else "")
            rows.append(f"{'🟢' if p.is_active else '⚪️'} <code>{escape(p.code)}</code> {p.kind}:{p.value} · {limit}{until}{extra}")
        return "\n".join(rows) or "Промокодов нет."

    @router.message(Command("promos"))
    async def promos_cmd(message: Message, session: AsyncSession, deps: Deps):
        await message.answer(HELP["promos"] + "\n\n" + await promos_text(session, deps, message.from_user.id))

    @router.message(Command("promo_add"))
    async def promo_add(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) < 3 or not args[2].isdigit():
            await message.answer(HELP["promos"])
            return
        promo = await _guard(message, svc.create_promo(session, deps.settings, message.from_user.id, args[0], args[1].lower(),
                                                       int(args[2]), args[3:]))
        if promo:
            await message.answer(f"✅ Промокод <code>{escape(promo.code)}</code> создан.")

    @router.message(Command("promo_set", "promo_on", "promo_off"))
    async def promo_set(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if not args:
            await message.answer(HELP["promos"])
            return
        changes = args[1:] if command.command == "promo_set" else [f"active={'on' if command.command == 'promo_on' else 'off'}"]
        promo = await _guard(message, svc.update_promo(session, deps.settings, message.from_user.id, args[0], changes))
        if promo:
            await message.answer(f"✅ <code>{escape(promo.code)}</code>: {'активен' if promo.is_active else 'выключен'}")

    @router.message(Command("promo_archive"))
    async def promo_archive(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        promo = await _guard(message, svc.archive_promo(session, deps.settings, message.from_user.id, args[0] if args else ""))
        if promo:
            await message.answer(f"🗄 <code>{escape(promo.code)}</code> перенесён в архив.")

    @router.message(Command("promo"))
    async def promo_info(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        res = await _guard(message, svc.promo_stats(session, deps.settings, message.from_user.id, args[0] if args else ""))
        if res:
            p, used, paid = res
            await message.answer(
                f"🎟️ <code>{escape(p.code)}</code> · {p.kind}:{p.value}\nАктиваций: {p.activations_count}"
                f"{'/' + str(p.max_activations) if p.max_activations else ''}\nСкидок использовано: {used} · оплаченных заказов: {paid}\n"
                f"Активен: {'да' if p.is_active else 'нет'} · Архив: {'да' if p.is_archived else 'нет'}\n"
                f"Описание: {escape(p.description or '—')}")

    # ----- settings -----
    async def settings_text(session: AsyncSession) -> str:
        values = await all_settings(session)
        rows = [f"<code>{k}</code> = {escape(v[:80])}{'…' if len(v) > 80 else ''}" for k, v in values.items()]
        return ("⚙️ <b>Настройки</b>\n\n" + "\n".join(rows) +
                "\n\nИзменить: <code>/set ключ значение</code>\nНапр.: <code>/set maintenance on</code>, "
                "<code>/set trial_enabled off</code>, <code>/set welcome_text Текст…</code>\n"
                "Контакты поддержки и ссылки на документы задаются в <code>app/branding.py</code>.")

    @router.message(Command("settings"))
    async def settings_cmd(message: Message, session: AsyncSession):
        await message.answer(await settings_text(session))

    @router.callback_query(F.data == "adm:settings")
    async def settings_cb(cb: CallbackQuery, session: AsyncSession):
        await render(cb, await settings_text(session), kb.admin_back())
        await cb.answer()

    @router.message(Command("set"))
    async def set_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        raw = (command.args or "").strip()
        if " " not in raw and raw:
            raw += " "
        key, _, value = raw.partition(" ")
        if not key:
            await message.answer(await settings_text(session))
            return
        stored = await _guard(message, svc.change_setting(session, deps.settings, message.from_user.id, key, value))
        if stored is not None:
            await message.answer(f"✅ <code>{escape(key)}</code> обновлён.")

    # ----- broadcast -----
    @router.message(Command("broadcast"))
    async def broadcast_start(message: Message, state: FSMContext):
        await state.set_state(BroadcastInput.text)
        await message.answer("📢 Отправь текст рассылки (HTML-разметка поддерживается). /cancel — отмена.")

    @router.callback_query(F.data == "adm:broadcast")
    async def broadcast_start_cb(cb: CallbackQuery, state: FSMContext):
        await state.set_state(BroadcastInput.text)
        await render(cb, "📢 Отправь текст рассылки (HTML-разметка поддерживается). /cancel — отмена.", kb.admin_back())
        await cb.answer()

    @router.message(StateFilter(BroadcastInput.text), F.text, ~F.text.startswith("/"))
    async def broadcast_preview(message: Message, session: AsyncSession, state: FSMContext):
        text = message.html_text[:3500]
        bc = Broadcast(admin_telegram_id=message.from_user.id, text=text)
        session.add(bc)
        await session.commit()
        await state.clear()
        await message.answer("👀 <b>Предпросмотр:</b>")
        try:
            await message.answer(text)
        except Exception:  # noqa: BLE001
            await message.answer("⚠️ Некорректная HTML-разметка. Начни заново: /broadcast")
            return
        await message.answer("Отправить всем пользователям?", reply_markup=kb.broadcast_confirm(bc.id))

    @router.callback_query(F.data.startswith("adm:bc:"))
    async def broadcast_confirm(cb: CallbackQuery, bot: Bot, session: AsyncSession, deps: Deps):
        _, _, action, bid = cb.data.split(":")
        svc.require_admin(deps.settings, cb.from_user.id)
        bc = await session.get(Broadcast, int(bid))
        if bc is None or bc.status != "draft":
            await cb.answer("Рассылка уже обработана", show_alert=True)
            return
        if action == "cancel":
            bc.status = "cancelled"
            await session.commit()
            await render(cb, "✖️ Рассылка отменена.", kb.admin_back())
            await cb.answer()
            return
        await svc.audit(session, cb.from_user.id, "broadcast_send", str(bc.id), length=len(bc.text))
        await session.commit()
        await render(cb, "🚀 Рассылка запущена. Пришлю отчёт по завершении.", kb.admin_back())
        await cb.answer()

        async def _run() -> None:
            done = await run_broadcast(deps.session_factory, bc.id, make_notifier(bot), deps.settings.broadcast_rate_per_second)
            await bot.send_message(cb.from_user.id, f"📢 Рассылка #{done.id} завершена: доставлено {done.sent}, "
                                                    f"заблокировали бота {done.blocked}, ошибок {done.failed}.")

        asyncio.create_task(_run())

    # ----- tickets -----
    async def tickets_text(session: AsyncSession) -> str:
        rows = [f"#{t.id} · {t.status} · <code>{u.telegram_id}</code> · {deps_fmt(t.created_at)}" for t, u in await open_tickets(session)]
        return "🎫 <b>Открытые обращения</b>\n\n" + ("\n".join(rows) or "Нет.") + \
            "\n\n<code>/reply ID текст</code> — ответить · <code>/close ID</code> — закрыть"

    def deps_fmt(dt: datetime) -> str:
        return f"{dt:%d.%m %H:%M} UTC"

    @router.message(Command("tickets"))
    async def tickets_cmd(message: Message, session: AsyncSession):
        await message.answer(await tickets_text(session))

    @router.callback_query(F.data == "adm:tickets")
    async def tickets_cb(cb: CallbackQuery, session: AsyncSession):
        await render(cb, await tickets_text(session), kb.admin_back())
        await cb.answer()

    @router.message(Command("reply"))
    async def reply_cmd(message: Message, command: CommandObject, bot: Bot, session: AsyncSession, deps: Deps):
        svc.require_admin(deps.settings, message.from_user.id)
        raw = (command.args or "").strip()
        tid, _, text = raw.partition(" ")
        if not tid.isdigit() or not text.strip():
            await message.answer("Формат: <code>/reply ID текст</code>")
            return
        res = await add_admin_reply(session, int(tid), text.strip())
        if res is None:
            await message.answer("Обращение не найдено.")
            return
        ticket, user = res
        await svc.audit(session, message.from_user.id, "ticket_reply", str(ticket.id))
        await session.commit()
        delivered = await make_notifier(bot)(user.telegram_id, texts.TICKET_REPLY.format(id=ticket.id, text=escape(text.strip())), None)
        await message.answer("✅ Ответ отправлен." if delivered else "⚠️ Пользователь заблокировал бота.")

    @router.message(Command("close"))
    async def close_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        if len(args) != 1 or not args[0].isdigit():
            await message.answer("Формат: <code>/close ID</code>")
            return
        ok = await close_ticket(session, int(args[0]))
        await svc.audit(session, message.from_user.id, "ticket_close", args[0])
        await session.commit()
        await message.answer("✅ Закрыто." if ok else "Не найдено.")

    # ----- audit / orders / payments -----
    async def audit_text(session: AsyncSession, deps: Deps, admin_id: int) -> str:
        rows = [f"{a.created_at:%d.%m %H:%M} · <code>{a.admin_telegram_id}</code> · {escape(a.action)} · {escape(a.target or '')}"
                for a in await svc.recent_audit(session, deps.settings, admin_id)]
        return "🧾 <b>Журнал аудита (UTC)</b>\n\n" + ("\n".join(rows) or "Пусто.")

    @router.message(Command("audit"))
    async def audit_cmd(message: Message, session: AsyncSession, deps: Deps):
        await message.answer(await audit_text(session, deps, message.from_user.id))

    @router.callback_query(F.data == "adm:audit")
    async def audit_cb(cb: CallbackQuery, session: AsyncSession, deps: Deps):
        await render(cb, await audit_text(session, deps, cb.from_user.id), kb.admin_back())
        await cb.answer()

    @router.message(Command("order"))
    async def order_cmd(message: Message, command: CommandObject, session: AsyncSession, deps: Deps):
        args = _args(command)
        order = await session.get(Order, args[0][:36]) if args else None
        if order is None:
            await message.answer("Заказ не найден. Формат: <code>/order ID</code>")
            return
        await message.answer(
            f"🧾 <code>{order.id}</code>\nСтатус: {order.status}{' (TEST)' if order.is_test else ''}\n"
            f"Тариф: {escape(order.plan_title)} · {order.duration_days} дн.\nСумма: {fmt_money(order.amount, order.currency)} "
            f"(скидка {fmt_money(order.discount, order.currency)})\nПровайдер: {order.provider} · платёж: {escape(order.provider_payment_id or '—')}\n"
            f"Создан: {deps.fmt_dt(order.created_at)}" + (f"\nОплачен: {deps.fmt_dt(order.paid_at)}" if order.paid_at else ""))

    @router.message(Command("testpay"))
    async def testpay_cmd(message: Message, command: CommandObject, bot: Bot, session: AsyncSession, deps: Deps):
        """DEVELOPMENT ONLY: emits a signed test event through the same verification path as the webhook."""
        provider = deps.payments
        if not isinstance(provider, TestPaymentProvider) or deps.settings.app_env == "production":
            await message.answer("Доступно только в тестовом режиме оплаты.")
            return
        args = _args(command)
        if len(args) != 2 or args[1] not in {"success", "fail"}:
            await message.answer("Формат: <code>/testpay ORDER_ID success|fail</code>")
            return
        order = await session.get(Order, args[0][:36])
        if order is None:
            await message.answer("Заказ не найден.")
            return
        body, headers = provider.build_event(order.id, args[1] == "success", order.amount, order.currency)
        event = provider.parse_webhook(headers, body)
        await svc.audit(session, message.from_user.id, "test_payment", order.id, result=args[1])
        await session.commit()
        result = await process_payment_event(session, event, now_utc())
        await message.answer(f"🧪 Тестовое событие обработано: {result.outcome.value}")
        await deliver_payment_result(bot, session, deps, result)

    @router.message(Command("confirm_payment"))
    async def confirm_payment_cmd(message: Message, command: CommandObject, bot: Bot, session: AsyncSession, deps: Deps):
        """Recovery for a Telegram payment that was charged but not recorded (e.g. DB outage)."""
        svc.require_admin(deps.settings, message.from_user.id)
        args = _args(command)
        if len(args) != 2:
            await message.answer("Формат: <code>/confirm_payment ORDER_ID TELEGRAM_CHARGE_ID</code>")
            return
        order = await session.get(Order, args[0][:36])
        if order is None or order.provider != "telegram":
            await message.answer("Заказ не найден или не через Telegram Payments.")
            return
        await svc.audit(session, message.from_user.id, "confirm_payment", order.id, charge_id=args[1])
        await session.commit()
        event = PaymentEventData("telegram", args[1], order.id, True, order.amount, order.currency)
        result = await process_payment_event(session, event, now_utc())
        await message.answer(f"Результат: {result.outcome.value}")
        await deliver_payment_result(bot, session, deps, result)

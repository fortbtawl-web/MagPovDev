"""Telegram admin menu. Access: Telegram ID in ADMIN_IDS (never username). All changes require a reason."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select

from backend import webapp_url
from backend.models import Plan
from backend.services import admin as admin_svc
from backend.services import grants as grants_svc
from backend.services import promo as promo_svc
from backend.services import roles
from backend.services import support as support_svc
from backend.services.durations import parse_spec
from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services import referrals as ref_svc
from backend.services import settings as settings_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_dt, fmt_money
from bot import ui
from bot.common import PLAN_NAMES, show, sub_line
from bot.keyboards import is_https

router = Router(name="admin")
SRC = "bot"


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, ctx: AppContext) -> bool:
        return event.from_user is not None and await roles.is_admin(ctx, event.from_user.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class AdminFSM(StatesGroup):
    find_user = State()
    value = State()
    reason = State()
    note = State()
    sup_text = State()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows])


HOME_BACK = [("‹ Админ-меню", "adm:home")]
CANCEL = [[("Отмена", "adm:home")]]
REASON_PROMPT = "Укажите причину (обязательно, сохраняется в журнале аудита):"
# Operations whose reason prompt must spell out the consequence.
DANGER = {
    "g_revoke": "Доступ будет отозван, ключи пользователя удалены.",
    "g_terminate": "Подписка будет завершена досрочно.",
    "user_block": "Пользователь будет заблокирован, VPN-доступ отключится.",
    "user_suspend": "VPN-доступ пользователя будет приостановлен.",
    "admin_revoke": "Права администратора будут сняты.",
    "plan_toggle": "Изменится доступность тарифа для покупки.",
    "promo_toggle": "Изменится доступность промокода.",
    "reward_reverse": "Начисление будет отменено, бонусные дни списаны.",
}

# op -> (prompt for value or None, needs value parsing)
OPS: dict[str, str | None] = {
    "user_add_days": "Сколько дней добавить? (1–3650)",
    "user_bonus": "Сколько бонусных дней начислить? Отрицательное число — списать.",
    "user_limit": "Новый лимит устройств (1–50):",
    "user_suspend": None,
    "user_resume": None,
    "plan_price": "Новая цена в рублях (например 199 или 199.50):",
    "plan_days": "Новый срок в днях (1–3650):",
    "plan_limit": "Новый лимит устройств (1–50):",
    "plan_toggle": None,
    "setting": "Новое значение (число, или да/нет):",
    "setting_toggle": None,
    "reward_reverse": None,
    "g_extend": "На сколько продлить? Примеры: 12h, 3d, 2w, 1m (часы, дни, недели, месяцы)",
    "g_replace": "Срок новой подписки (от текущего момента): 12h, 3d, 2w, 1m",
    "g_set_end": "Дата окончания: ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ (время МСК)",
    "g_lifetime": None,
    "g_shorten": "На сколько сократить? Примеры: 12h, 3d, 1w",
    "g_terminate": None,
    "g_revoke": None,
    "user_block": None,
    "user_unblock": None,
    "user_support_reset": None,
    "admin_add": "Кого назначить администратором? Telegram ID, @username или SV-ID:",
    "mod_add": "Кого назначить модератором? Telegram ID, @username или SV-ID:",
    "user_coins": "Сколько SpirCoin начислить? Отрицательное число — списать (например 100 или -50):",
    "claim_ok": None,
    "claim_no": None,
    "admin_revoke": None,
    "promo_create": ui.screen(
        ui.title("Новый промокод", "Одной строкой"),
        "<code>КОД ТИП ЗНАЧЕНИЕ [ЛИМИТ] [ДНЕЙ_ДЕЙСТВИЯ]</code>",
        ui.card(
            ui.kv("ТИП", "percent (скидка %), fixed (скидка ₽), days (бесплатные дни)"),
            ui.kv("КОД", "«-» — сгенерировать автоматически"),
            ui.kv("ЛИМИТ", "«-» — без лимита"),
        ),
        ui.kv("Примеры", "\n<code>SUMMER percent 20 100 30</code>\n<code>- days 7 50</code>\n<code>GIFT fixed 50</code>"),
    ),
    "promo_toggle": None,
}


@router.message(Command("admin"))
async def cmd_admin(message: Message, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await home_text(ctx), reply_markup=home_kb(ctx))


async def home_text(ctx: AppContext) -> str:
    from backend.services import coins as coins_svc

    async with ctx.session() as session:
        s = await admin_svc.stats(session)
        e = await coins_svc.economy_stats(session)
    return ui.screen(
        ui.title("Администрирование", "SpirittVPN · панель управления"),
        ui.card(
            ui.kv("Пользователи", f"{s['users_total']} (+{s['users_new_24h']} за сутки, +{s['users_new_7d']} за неделю)"),
            ui.kv("Пробных периодов", s["trials_total"]),
            ui.kv("Активных устройств", s["devices_active"]),
        ),
        ui.card(
            ui.kv("Активные подписки", f"{s['subs_active']} · платных/выданных {s['subs_paid_active']} · бессрочных {s['subs_lifetime']}"),
            ui.kv("Истёкшие", s["subs_expired"]),
            ui.kv("Приостановлено", s["subs_suspended"]),
            ui.kv("Доход", f"{fmt_money(s['revenue_minor'])} · заказов {s['orders_paid']}"),
        ),
        ui.card(
            ui.kv("Приглашений", f"{s['referrals_total']} · с пробным {s['referrals_with_trial']} · с оплатой {s['referrals_with_payment']}"),
            ui.kv("Бонусных дней начислено", s["bonus_days_granted"]),
        ),
        ui.card(
            ui.kv("VPN-операций в очереди", s["vpn_ops_pending"]),
            ui.kv("Ошибок за сутки", s["vpn_ops_failed_24h"]),
        ),
        ui.card(
            ui.kv("SpirCoin", f"выдано {e['coins_issued']} · потрачено {e['coins_spent']} · на балансах {e['coins_in_circulation']}"),
            ui.kv("Покупок за SpirCoin", e["coin_orders"]),
            ui.kv("Заданий выполнено", f"{e['tasks_rewarded']} · на проверке {e['claims_pending']}"),
        ),
    )


def home_kb(ctx: AppContext) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👤 Пользователь", callback_data="adm:find"), InlineKeyboardButton(text="💬 Обращения", callback_data="adm:tickets")],
        [InlineKeyboardButton(text="Администраторы", callback_data="adm:admins")],
        [InlineKeyboardButton(text="💎 Тарифы", callback_data="adm:plans"), InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm:promos")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="adm:ref"), InlineKeyboardButton(text="🎯 Задания", callback_data="adm:claims")],
        [InlineKeyboardButton(text="⚙ Настройки", callback_data="adm:settings"), InlineKeyboardButton(text="Серверы", callback_data="adm:nodes")],
        [InlineKeyboardButton(text="Ошибки VPN", callback_data="adm:errors"), InlineKeyboardButton(text="Журнал", callback_data="adm:audit")],
    ]
    _app = webapp_url.current(ctx.settings)
    base = _app.rstrip("/") if _app else ""
    if is_https(base):
        rows.append([InlineKeyboardButton(text="Веб-панель", web_app=WebAppInfo(url=f"{base}/admin"))])
    rows.append([InlineKeyboardButton(text="⌂ Главное меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adm:home")
async def adm_home(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await show(cb, await home_text(ctx), home_kb(ctx))


# ---------- users ----------

@router.callback_query(F.data == "adm:find")
async def adm_find(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.find_user)
    await show(cb, ui.screen(ui.title("Поиск пользователя"), "Отправьте Telegram ID, @username или SpirittVPN ID (SV-XXXXXX)."), kb([HOME_BACK]))


@router.message(StateFilter(AdminFSM.find_user), F.text)
async def adm_find_value(message: Message, ctx: AppContext, state: FSMContext) -> None:
    q = (message.text or "").strip()[:64]
    async with ctx.session() as session:
        found = await admin_svc.find_users(session, q, limit=10)
    if not found:
        await message.answer(ui.warn("Никого не нашли. Попробуйте другой запрос или вернитесь в админ-меню."), reply_markup=kb([HOME_BACK]))
        return
    await state.clear()
    if len(found) == 1:
        await send_user_card(message, ctx, found[0].id)
        return
    rows = [[(f"{u.public_id} · {u.display_name}{' @' + u.username if u.username else ''}", f"adm:user:{u.id}")] for u in found]
    rows.append(HOME_BACK)
    await message.answer(ui.screen(ui.title("Поиск пользователя", "Найдено несколько")), reply_markup=kb(rows))


@router.callback_query(F.data.startswith("adm:user:"))
async def adm_user(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await send_user_card(cb, ctx, int(cb.data.split(":")[2]))


async def user_card(ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with ctx.session() as session:
        d = await admin_svc.user_details(session, user_id)
    u, sub = d["user"], d["subscription"]
    used = sum(1 for x in d["devices"] if x.status in ("pending", "active", "expired", "suspended", "revoking"))
    paid = [o for o in d["orders"] if o.status == "paid"]
    role = await roles.get_role(ctx, u.id)
    flags = (" · 🔴 заблокирован" if u.is_banned else "") + ({"owner": " · владелец", "admin": " · админ", "moderator": " · модератор"}.get(role or "", ""))
    devices = ", ".join(f"{ui.safe(x.label)} [{x.status}] {x.node.flag}" for x in d["devices"][:8]) or "—"
    orders = "\n".join(f"№{o.id} {pay_svc.order_line(o)}" for o in d["orders"][:5]) or "—"
    text = ui.screen(
        ui.title(f"{ui.safe(u.display_name)} {('@' + ui.safe(u.username)) if u.username else ''}".strip(), flags.strip(" ·") or None),
        ui.card(ui.kv("SpirittVPN ID", f"<code>{u.public_id}</code>"), ui.kv("Telegram ID", f"<code>{u.id}</code>"), ui.kv("Регистрация", fmt_dt(u.created_at))),
        ui.card(ui.kv("Тариф", PLAN_NAMES.get(sub.plan_code, sub.plan_code) if sub else "—"), sub_line(sub, used if sub else None)),
        ui.card(ui.kv("Устройства", devices)),
        ui.card(ui.kv("Оплаты", f"{len(paid)} на {fmt_money(sum(o.amount_minor for o in paid))}"), ui.kv("Последние заказы", "\n" + orders)),
        ui.card(
            ui.kv("Бонус", f"{d['referrals']['balance_days']} дн. · приглашено {d['referrals']['invited']} · пригласил {d['referrer_id'] or '—'}"),
            ui.kv("Пробный период", d["trial"].status if d["trial"] else "не активировался"),
            ui.kv("История бонусов", "; ".join(f"{b.days:+d} {b.kind}" for b in d["bonus"][:5]) or "—"),
        ),
    )
    rows = [
        [("Подписка: выдать / изменить", f"adm:grant:{u.id}")],
        [("Лимит устройств", f"adm:op:user_limit:{u.id}"), ("Бонус ±", f"adm:op:user_bonus:{u.id}")],
        [("🪙 SpirCoin ±", f"adm:op:user_coins:{u.id}")],
        [("Сбросить лимит обращений", f"adm:op:user_support_reset:{u.id}")],
        [("Разблокировать", f"adm:op:user_unblock:{u.id}")] if u.is_banned else [("Заблокировать", f"adm:op:user_block:{u.id}")],
    ]
    if sub and sub.status == "suspended":
        rows.append([("Восстановить доступ", f"adm:op:user_resume:{u.id}")])
    elif sub:
        rows.append([("Приостановить доступ", f"adm:op:user_suspend:{u.id}")])
    rows.append(HOME_BACK)
    return text, kb(rows)


async def send_user_card(event: Message | CallbackQuery, ctx: AppContext, user_id: int) -> None:
    try:
        text, markup = await user_card(ctx, user_id)
    except ServiceError as exc:
        await show(event, ui.warn(ui.safe(exc.message)), kb([HOME_BACK]))
        return
    await show(event, text, markup)


@router.callback_query(F.data.startswith("adm:grant:"))
async def adm_grant(cb: CallbackQuery, ctx: AppContext) -> None:
    uid = cb.data.split(":")[2]
    rows = [
        [("Продлить", f"adm:op:g_extend:{uid}"), ("Новая подписка", f"adm:op:g_replace:{uid}")],
        [("Точная дата окончания", f"adm:op:g_set_end:{uid}"), ("♾ Бессрочно", f"adm:op:g_lifetime:{uid}")],
        [("Сократить", f"adm:op:g_shorten:{uid}"), ("Завершить досрочно", f"adm:op:g_terminate:{uid}")],
        [("Отозвать доступ (удалить ключи)", f"adm:op:g_revoke:{uid}")],
        [("‹ Пользователь", f"adm:user:{uid}")],
    ]
    await show(
        cb,
        ui.screen(
            ui.title("Подписка пользователя", "Выдача и изменение"),
            ui.note("Продление добавляет срок к текущей дате окончания (если подписка активна). После выбора укажите срок, причину (видна только админам) и, по желанию, комментарий для пользователя."),
        ),
        kb(rows),
    )


# ---------- generic "value + reason" operations ----------

@router.callback_query(F.data.startswith("adm:op:"))
async def adm_op(cb: CallbackQuery, state: FSMContext) -> None:
    _, _, op, target = cb.data.split(":", 3)
    if op not in OPS:
        await cb.answer("Неизвестная операция", show_alert=True)
        return
    await state.update_data(op=op, target=target, value=None)
    prompt = OPS[op]
    if prompt:
        await state.set_state(AdminFSM.value)
        await show(cb, ui.screen(ui.title("Операция"), prompt), kb(CANCEL))
    else:
        await state.set_state(AdminFSM.reason)
        danger = DANGER.get(op)
        await show(cb, ui.screen(ui.title("Подтверждение"), ui.warn(danger) if danger else "", REASON_PROMPT), kb(CANCEL))


@router.message(StateFilter(AdminFSM.value), F.text)
async def adm_op_value(message: Message, state: FSMContext) -> None:
    await state.update_data(value=(message.text or "").strip())
    await state.set_state(AdminFSM.reason)
    await message.answer(ui.screen(ui.title("Причина"), REASON_PROMPT), reply_markup=kb(CANCEL))


def _int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise ServiceError("Нужно целое число.", "bad_value") from exc


def _rub_to_minor(value: Any) -> int:
    try:
        return int((Decimal(str(value).replace(",", ".").strip()) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise ServiceError("Нужна сумма в рублях, например 199.", "bad_value") from exc


@router.message(StateFilter(AdminFSM.reason), F.text)
async def adm_op_reason(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    reason = admin_svc.require_reason(message.text)
    if data["op"].startswith("g_"):
        await state.update_data(reason=reason)
        await state.set_state(AdminFSM.note)
        await message.answer(ui.screen(ui.title("Комментарий"), "Комментарий для пользователя (например «Компенсация») или «-», чтобы пропустить."), reply_markup=kb(CANCEL))
        return
    await state.clear()
    await _run(message, ctx, data, reason, None)


@router.message(StateFilter(AdminFSM.note), F.text)
async def adm_op_note(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    note = (message.text or "").strip()
    await _run(message, ctx, data, data["reason"], None if note in ("-", "") else note[:200])


async def _run(message: Message, ctx: AppContext, data: dict, reason: str, note: str | None) -> None:
    op, target, value = data["op"], data["target"], data.get("value")
    result = await execute(ctx, message.from_user.id, op, target, value, reason, note)
    await message.answer(ui.ok(result), reply_markup=kb([HOME_BACK]))
    if op.startswith(("user_", "g_")):
        await send_user_card(message, ctx, int(target))
    elif op.startswith("admin_"):
        text, markup = await admins_view(ctx, message.from_user.id)
        await message.answer(text, reply_markup=markup)


async def execute(ctx: AppContext, admin_id: int, op: str, target: str, value: Any, reason: str, note: str | None = None) -> str:
    if op.startswith("g_"):
        mode = op[2:]
        kwargs: dict[str, Any] = {}
        if mode in ("extend", "replace", "shorten"):
            spec = parse_spec(str(value or ""))
            if spec is None or spec[0] <= 0:
                raise ServiceError("Срок в формате 12h, 3d, 2w или 1m.", "bad_duration")
            kwargs["amount"], kwargs["unit"] = spec
        elif mode == "set_end":
            kwargs["ends_at"] = str(value or "")
        res = await grants_svc.grant(ctx, admin_id, SRC, int(target), mode=mode, reason=reason, note=note, **kwargs)
        v = res.vpn
        return f"{res.message}\nVPN: подтверждено {v['synced'] + v['revoked']}, ошибок {v['failed']}, в очереди {v['pending']}" + (
            f", устройство создано: {'да' if v['provisioned'] else 'нет'}" if v["provisioned"] is not None else ""
        )
    if op == "user_support_reset":
        used = await admin_svc.reset_support_limit(ctx, admin_id, SRC, int(target), reason)
        return f"Лимит обращений сброшен (было использовано: {used}). Пользователь может писать в поддержку снова."
    if op in ("user_block", "user_unblock"):
        _, ok, failed = await admin_svc.set_blocked(ctx, admin_id, SRC, int(target), reason, blocked=op == "user_block")
        return f"{'Заблокирован' if op == 'user_block' else 'Разблокирован'}. VPN: подтверждено {ok}, ошибок {failed}."
    if op == "promo_create":
        parts = str(value or "").split()
        if len(parts) < 3:
            raise ServiceError("Формат: КОД ТИП ЗНАЧЕНИЕ [ЛИМИТ] [ДНЕЙ_ДЕЙСТВИЯ]", "bad_value")
        code, kind = (None if parts[0] == "-" else parts[0]), parts[1].lower()
        number = _rub_to_minor(parts[2]) if kind == "fixed" else _int(parts[2])
        limit = _int(parts[3]) if len(parts) > 3 and parts[3] != "-" else None
        expires = None
        if len(parts) > 4:
            from datetime import timedelta

            from backend.utils import utcnow

            expires = utcnow() + timedelta(days=_int(parts[4]))
        p = await promo_svc.create(ctx, admin_id, SRC, code=code, kind=kind, value=number, max_activations=limit, expires_at=expires, reason=reason)
        return f"Промокод <code>{p.code}</code> создан: {promo_svc.value_label(p.kind, p.value)}, лимит {p.max_activations or '∞'}, до {fmt_dt(p.expires_at) if p.expires_at else 'бессрочно'}"
    if op == "promo_toggle":
        from backend.models import PromoCode

        async with ctx.session() as session:
            cur = await session.get(PromoCode, int(target))
        if cur is None:
            raise ServiceError("Промокод не найден.", "not_found")
        p = await promo_svc.set_active(ctx, admin_id, SRC, int(target), not cur.is_active, reason)
        return f"Промокод {p.code} {'включён' if p.is_active else 'отключён'}"
    if op == "admin_add":
        async with ctx.session() as session:
            user = await admin_svc.resolve_user(session, str(value or ""))
        await roles.grant_admin(ctx, admin_id, user.id, reason, SRC)
        return f"{user.display_name} ({user.public_id}) назначен администратором."
    if op == "mod_add":
        async with ctx.session() as session:
            user = await admin_svc.resolve_user(session, str(value or ""))
        await roles.grant_admin(ctx, admin_id, user.id, reason, SRC, role="moderator")
        return f"{user.display_name} ({user.public_id}) назначен модератором."
    if op == "user_coins":
        from backend.services import coins as coins_svc

        new = await coins_svc.admin_adjust(ctx, admin_id, SRC, int(target), _int(value), reason)
        return f"Баланс SpirCoin: {coins_svc.fmt_coins(new)}"
    if op in ("claim_ok", "claim_no"):
        from backend.services import tasks as tasks_svc

        c = await tasks_svc.review(ctx, admin_id, SRC, int(target), approve=op == "claim_ok", reason=reason)
        return "Награда выдана." if c.status == "rewarded" else "Заявка отклонена."
    if op == "admin_revoke":
        await roles.revoke_admin(ctx, admin_id, int(target), reason, SRC)
        return f"Права администратора {target} сняты."
    if op == "user_add_days":
        sub = await admin_svc.add_days(ctx, admin_id, SRC, int(target), _int(value), reason)
        return f"Подписка: {sub_line(sub)}"
    if op == "user_bonus":
        balance = await admin_svc.adjust_bonus(ctx, admin_id, SRC, int(target), _int(value), reason)
        return f"Бонусный баланс: {balance} дн."
    if op == "user_limit":
        sub = await admin_svc.set_device_limit(ctx, admin_id, SRC, int(target), _int(value), reason)
        return f"Лимит устройств: {sub.device_limit}"
    if op in ("user_suspend", "user_resume"):
        ok, failed = await admin_svc.suspend(ctx, admin_id, SRC, int(target), reason, suspended=op == "user_suspend")
        tail = f" Не подтверждено сервером: {failed} — повторим автоматически." if failed else ""
        return f"{'Доступ приостановлен' if op == 'user_suspend' else 'Доступ восстановлен'}. Подтверждено сервером: {ok}.{tail}"
    if op == "plan_price":
        p = await admin_svc.update_plan(ctx, admin_id, SRC, int(target), {"price_minor": _rub_to_minor(value)}, reason)
        return f"Цена «{p.title}»: {fmt_money(p.price_minor)}"
    if op == "plan_days":
        p = await admin_svc.update_plan(ctx, admin_id, SRC, int(target), {"duration_days": _int(value)}, reason)
        return f"Срок «{p.title}»: {p.duration_days} дн."
    if op == "plan_limit":
        p = await admin_svc.update_plan(ctx, admin_id, SRC, int(target), {"device_limit": _int(value)}, reason)
        return f"Лимит «{p.title}»: {p.device_limit}"
    if op == "plan_toggle":
        async with ctx.session() as session:
            p = await session.get(Plan, int(target))
        p = await admin_svc.update_plan(ctx, admin_id, SRC, int(target), {"is_active": not p.is_active}, reason)
        return f"Тариф «{p.title}» {'включён' if p.is_active else 'отключён'}"
    if op == "setting":
        new = await admin_svc.update_setting(ctx, admin_id, SRC, target, value, reason)
        return f"{target} = {new}"
    if op == "setting_toggle":
        async with ctx.session() as session:
            cur = await settings_svc.get(session, target)
        new = await admin_svc.update_setting(ctx, admin_id, SRC, target, not cur, reason)
        return f"{settings_svc.SETTINGS_SCHEMA[target][2]}: {'да' if new else 'нет'}"
    if op == "reward_reverse":
        r = await admin_svc.reverse_reward(ctx, admin_id, SRC, int(target), reason)
        return f"Начисление #{r.id} отменено (−{r.days} дн.)"
    raise ServiceError("Неизвестная операция", "bad_op")


# ---------- plans ----------

@router.callback_query(F.data == "adm:plans")
async def adm_plans(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        plans = await pay_svc.list_plans(session, only_active=False)
    rows = [[(f"{'🟢' if p.is_active else '⚪'} {p.title} · {fmt_money(p.price_minor)}", f"adm:plan:{p.id}")] for p in plans]
    rows.append(HOME_BACK)
    await show(cb, ui.screen(ui.title("Тарифы", "Выберите тариф для изменения"), ui.note("Создание новых тарифов — в веб-панели.")), kb(rows))


@router.callback_query(F.data.startswith("adm:plan:"))
async def adm_plan(cb: CallbackQuery, ctx: AppContext) -> None:
    plan_id = int(cb.data.split(":")[2])
    async with ctx.session() as session:
        p = await session.get(Plan, plan_id)
    if p is None:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    text = ui.screen(ui.title(ui.safe(p.title), f"<code>{ui.safe(p.code)}</code>"), ui.card(ui.kv("Цена", fmt_money(p.price_minor)), ui.kv("Условия", pay_svc.plan_terms(p)), ui.kv("Статус", "🟢 включён" if p.is_active else "⚪ отключён")))
    rows = [[("Цена", f"adm:op:plan_price:{p.id}"), ("Лимит устройств", f"adm:op:plan_limit:{p.id}")]]
    if not p.is_lifetime:
        rows.append([("Срок", f"adm:op:plan_days:{p.id}")])
    rows.append([("Отключить" if p.is_active else "Включить", f"adm:op:plan_toggle:{p.id}")])
    rows.append([("‹ Тарифы", "adm:plans")])
    await show(cb, text, kb(rows))


# ---------- referrals & settings ----------

@router.callback_query(F.data == "adm:ref")
async def adm_ref(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        cfg = await settings_svc.get_all(session)
        s = await admin_svc.stats(session)
        rewards = await admin_svc.reward_history(session, limit=8)
    yn = lambda v: "🟢 вкл" if v else "⚪ выкл"  # noqa: E731
    reward_lines = [f"#{r.id} {ui.safe(r.event_type)} +{r.days} дн. → {r.referrer_id} [{ui.safe(r.status)}]" for r in rewards]
    text = ui.screen(
        ui.title("Реферальная программа"),
        ui.card(
            ui.kv("Программа", yn(cfg["referral_enabled"])),
            ui.kv("Бонус за пробный период", f"{yn(cfg['referral_trial_bonus_enabled'])} · {cfg['referral_trial_bonus_days']} дн."),
            ui.kv("Бонус за первую оплату", f"{yn(cfg['referral_payment_bonus_enabled'])} · {cfg['referral_payment_bonus_days']} дн."),
        ),
        ui.card(
            ui.kv("Приглашений", f"{s['referrals_total']} · с пробным {s['referrals_with_trial']} · с оплатой {s['referrals_with_payment']}"),
            ui.kv("Начислено дней", s["bonus_days_granted"]),
        ),
        ui.kv("Последние начисления", "") + "\n" + (ui.card(*reward_lines) if reward_lines else ui.card("○ Начислений нет")),
    )
    rows = [
        [("Программа вкл/выкл", "adm:op:setting_toggle:referral_enabled")],
        [("Пробный: вкл/выкл", "adm:op:setting_toggle:referral_trial_bonus_enabled"), ("Пробный: дни", "adm:op:setting:referral_trial_bonus_days")],
        [("Оплата: вкл/выкл", "adm:op:setting_toggle:referral_payment_bonus_enabled"), ("Оплата: дни", "adm:op:setting:referral_payment_bonus_days")],
    ]
    rows += [[(f"Отменить начисление #{r.id}", f"adm:op:reward_reverse:{r.id}")] for r in rewards if r.status == "granted"][:4]
    rows.append(HOME_BACK)
    await show(cb, text, kb(rows))


@router.callback_query(F.data == "adm:settings")
async def adm_settings(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        cfg = await settings_svc.get_all(session)
    lines, rows = [], []
    for key, (_, typ, label) in settings_svc.SETTINGS_SCHEMA.items():
        val = cfg[key]
        lines.append(ui.kv(ui.safe(label), ('да' if val else 'нет') if typ is bool else ui.safe(val)))
        rows.append([(label[:40], f"adm:op:{'setting_toggle' if typ is bool else 'setting'}:{key}")])
    rows.append(HOME_BACK)
    await show(cb, ui.screen(ui.title("Настройки", "Нажмите параметр, чтобы изменить"), ui.card(*lines)), kb(rows))


# ---------- infrastructure ----------

@router.callback_query(F.data == "adm:nodes")
async def adm_nodes(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        nodes = await admin_svc.nodes_overview(session)
    if not nodes:
        text = ui.screen(ui.title("Серверы"), ui.card("○ Серверы не настроены"), ui.note("Заполните VPN_NODES и VPN_NODE_&lt;CODE&gt;_TOKEN в .env."))
    else:
        cards = []
        for n in nodes:
            health = "🟢 API доступен" if n["health_ok"] else "🔴 недоступен" if n["health_ok"] is False else "⚪ не проверялась"
            cards.append(ui.card(
                f"{n['flag']} <b>{ui.safe(n['name'])}</b> ({ui.safe(n['code'])})",
                ui.kv("Состояние", health),
                ui.kv("Активных устройств", n["active_devices"]),
                ui.kv("Клиентов на ноде", n["remote_clients"] if n["remote_clients"] is not None else "—"),
                ui.kv("Проверка", fmt_dt(n["health_at"])),
                ui.warn(ui.safe(n["last_error"])) if n["last_error"] else "",
            ))
        text = ui.screen(ui.title("Серверы"), *cards)
    await show(cb, text, kb([[("↻ Проверить сейчас", "adm:nodes_check")], HOME_BACK]))


@router.callback_query(F.data == "adm:nodes_check")
async def adm_nodes_check(cb: CallbackQuery, ctx: AppContext) -> None:
    await cb.answer("Проверяем ноды…")
    await dev_svc.check_nodes(ctx)
    await adm_nodes(cb, ctx)


@router.callback_query(F.data == "adm:errors")
async def adm_errors(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        ops = await admin_svc.recent_vpn_ops(session, only_problems=True, limit=15)
    text = ui.screen(
        ui.title("Ошибки VPN", "Проблемные операции очереди"),
        ui.card(*(f"#{o.id} {ui.safe(o.op_type)} dev={o.device_id} [{ui.safe(o.status)}] попыток {o.attempts}\n{ui.safe((o.last_error or '')[:120])}" for o in ops)) if ops else ui.card("🟢 Проблем нет"),
    )
    await show(cb, text, kb([HOME_BACK]))


@router.callback_query(F.data == "adm:audit")
async def adm_audit(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        items = await admin_svc.recent_audit(session, limit=15)
    text = ui.screen(
        ui.title("Журнал", "Последние действия администраторов"),
        ui.card(*(f"{fmt_dt(a.created_at)} · {a.admin_id} · {ui.safe(a.action)} {ui.safe(a.entity_type)}#{a.entity_id}\n«{ui.safe(a.reason[:80])}»" for a in items)) if items else ui.card("○ Записей нет"),
    )
    await show(cb, text, kb([HOME_BACK]))


# ---------- promo codes ----------

@router.callback_query(F.data == "adm:promos")
async def adm_promos(cb: CallbackQuery, ctx: AppContext) -> None:
    items = await promo_svc.list_promos(ctx, limit=15)
    lines = []
    for p in items:
        state = "🟢" if p.is_active else "⚪"
        lines.append(
            f"{state} <code>{ui.safe(p.code)}</code> · {promo_svc.value_label(p.kind, p.value)} · {p.activations}/{p.max_activations or '∞'}"
            f" · {('до ' + fmt_dt(p.expires_at)) if p.expires_at else 'бессрочно'}"
        )
    rows = [[("＋ Создать промокод", "adm:op:promo_create:0")]]
    rows += [[(f"{'Отключить' if p.is_active else 'Включить'} {p.code}", f"adm:op:promo_toggle:{p.id}")] for p in items[:8]]
    rows.append(HOME_BACK)
    await show(cb, ui.screen(ui.title("Промокоды"), ui.card(*lines) if lines else ui.card("○ Промокодов пока нет")), kb(rows))


# ---------- admins ----------

async def admins_view(ctx: AppContext, viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    entries = await roles.list_admins(ctx)
    owner = await roles.is_owner(ctx, viewer_id)
    lines = [
        f"{'<b>владелец</b>' if e['role'] == 'owner' else '<b>модератор</b>' if e['role'] == 'moderator' else '<b>админ</b>'} · {ui.safe(e['first_name'] or '—')} {('@' + ui.safe(e['username'])) if e['username'] else ''} · <code>{e['user_id']}</code>"
        f" · {ui.safe(e['public_id'] or 'нет в БД')} · {'.env' if e['source'] == 'env' else 'панель'}"
        for e in entries
    ]
    rows: list[list[tuple[str, str]]] = []
    if owner:
        rows.append([("＋ Администратор", "adm:op:admin_add:0"), ("＋ Модератор", "adm:op:mod_add:0")])
        rows += [[(f"Снять {e['first_name'] or e['user_id']}", f"adm:op:admin_revoke:{e['user_id']}")] for e in entries if e["source"] == "db" and e["role"] in ("admin", "moderator")]
    rows.append(HOME_BACK)
    text = ui.screen(ui.title("Администраторы"), ui.card(*lines) if lines else ui.card("○ Список пуст"))
    if not owner:
        text += "\n\n" + ui.note("Назначать и снимать администраторов может только главный администратор.")
    return text, kb(rows)


@router.callback_query(F.data == "adm:admins")
async def adm_admins(cb: CallbackQuery, ctx: AppContext) -> None:
    await show(cb, *await admins_view(ctx, cb.from_user.id))


# ---------- task claims ----------

@router.callback_query(F.data == "adm:claims")
async def adm_claims(cb: CallbackQuery, ctx: AppContext) -> None:
    from backend.services import tasks as tasks_svc

    items = await tasks_svc.claims_list(ctx, status="pending", limit=15)
    lines = [f"#{c['id']} · {ui.safe(c['task_title'])} · {ui.safe(c['user_name'])} (<code>{c['user_id']}</code>)" for c in items]
    rows = [[(f"✓ #{c['id']}", f"adm:op:claim_ok:{c['id']}"), (f"✕ #{c['id']}", f"adm:op:claim_no:{c['id']}"), ("Профиль", f"adm:user:{c['user_id']}")] for c in items]
    rows.append(HOME_BACK)
    await show(cb, ui.screen(ui.title("Задания", "Заявки на проверку"), ui.card(*lines) if lines else ui.card("○ Новых заявок нет")), kb(rows))


# ---------- support tickets ----------

T_STATUS_BTNS = [("В работе", "in_progress"), ("Ждём ответа", "waiting_user"), ("Решено", "resolved"), ("Закрыть", "closed")]


@router.callback_query(F.data.startswith("adm:tickets"))
async def adm_tickets(cb: CallbackQuery, ctx: AppContext) -> None:
    flt = cb.data.split(":")[2] if cb.data.count(":") >= 2 else "open"
    kwargs = {"open": {"status": "open"}, "new": {"status": "new"}, "mine": {"status": "open", "assigned": "me"}}.get(flt, {"status": "open"})
    rows_data = await support_svc.admin_list(ctx, cb.from_user.id, limit=20, **kwargs)
    icons = {"new": "🟡", "in_progress": "🟠", "waiting_user": "🟡", "resolved": "🟢", "closed": "⚪"}
    rows = [[(f"{icons.get(t.status, '')} {t.number} · {u.display_name[:14]} · {t.subject[:20]}", f"sup:a:{t.id}")] for t, u, _ in rows_data]
    rows.append([("Открытые", "adm:tickets:open"), ("Новые", "adm:tickets:new"), ("Мои", "adm:tickets:mine")])
    rows.append(HOME_BACK)
    counts = await support_svc.counts(ctx)
    await show(cb, ui.screen(ui.title("Обращения", f"Фильтр: {ui.safe(flt)}"), ui.card(ui.kv("Новых", counts["support_new"]), ui.kv("Открытых", counts["support_open"]))), kb(rows))


async def ticket_view(ctx: AppContext, ticket_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from bot.handlers.support import thread_text

    t, u, msgs = await support_svc.admin_thread(ctx, ticket_id)
    head = ui.card(ui.kv("Пользователь", f"{ui.safe(u.display_name)} · {u.public_id} · <code>{u.id}</code>"), ui.kv("Ответственный", t.assigned_admin_id or "—")) + "\n\n"
    rows = [
        [("Ответить", f"adm:sr:{t.id}"), ("Заметка", f"adm:sn:{t.id}")],
        [(label, f"adm:ss:{t.id}:{st}") for label, st in T_STATUS_BTNS[:2]],
        [(label, f"adm:ss:{t.id}:{st}") for label, st in T_STATUS_BTNS[2:]],
        [("Взять себе", f"adm:sm:{t.id}"), ("Профиль", f"adm:user:{u.id}")],
    ]
    if any(m.attachment_file_id for m in msgs):
        rows.append([("Вложения", f"adm:satt:{t.id}")])
    rows.append([("‹ Обращения", "adm:tickets:open")])
    return head + thread_text(t, msgs, admin=True), kb(rows)


@router.callback_query(F.data.startswith("sup:a:"))
async def adm_ticket(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await show(cb, *await ticket_view(ctx, int(cb.data.split(":")[2])))


@router.message(Command("ticket"))
async def cmd_ticket(message: Message, ctx: AppContext) -> None:
    from aiogram.filters import CommandObject  # noqa: F401

    arg = (message.text or "").split(maxsplit=1)
    t = await support_svc.find_by_number(ctx, arg[1]) if len(arg) > 1 else None
    if t is None:
        await message.answer(ui.warn("Использование: /ticket SUP-12345"))
        return
    text, markup = await ticket_view(ctx, t.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith(("adm:sr:", "adm:sn:")))
async def adm_ticket_reply(cb: CallbackQuery, state: FSMContext) -> None:
    _, kind, tid = cb.data.split(":")
    await state.set_state(AdminFSM.sup_text)
    await state.update_data(ticket_id=int(tid), internal=kind == "sn")
    prompt = ui.screen(ui.title("Внутренняя заметка"), "Пользователь её не увидит.") if kind == "sn" else ui.screen(ui.title("Ответ пользователю"), "Сообщение будет отправлено ботом.")
    await show(cb, prompt, kb([[("Отмена", f"sup:a:{tid}")]]))


@router.message(StateFilter(AdminFSM.sup_text), F.text)
async def adm_ticket_reply_value(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await support_svc.admin_reply(ctx, message.from_user.id, data["ticket_id"], message.text, internal=data["internal"])
    await message.answer(ui.ok("Заметка сохранена" if data["internal"] else "Ответ отправлен пользователю"))
    text, markup = await ticket_view(ctx, data["ticket_id"])
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:ss:"))
async def adm_ticket_status(cb: CallbackQuery, ctx: AppContext) -> None:
    _, _, tid, status = cb.data.split(":")
    await support_svc.set_status(ctx, cb.from_user.id, int(tid), status)
    await show(cb, *await ticket_view(ctx, int(tid)))


@router.callback_query(F.data.startswith("adm:sm:"))
async def adm_ticket_take(cb: CallbackQuery, ctx: AppContext) -> None:
    tid = int(cb.data.split(":")[2])
    await support_svc.assign(ctx, cb.from_user.id, tid, cb.from_user.id)
    await show(cb, *await ticket_view(ctx, tid))


@router.callback_query(F.data.startswith("adm:satt:"))
async def adm_ticket_attachments(cb: CallbackQuery, ctx: AppContext) -> None:
    _, _, msgs = await support_svc.admin_thread(ctx, int(cb.data.split(":")[2]))
    await cb.answer()
    for m in [m for m in msgs if m.attachment_file_id][-5:]:
        if m.attachment_kind == "photo":
            await cb.message.answer_photo(m.attachment_file_id, caption=f"#{m.id}")
        else:
            await cb.message.answer_document(m.attachment_file_id, caption=f"#{m.id}")


# Non-admins hitting /admin get the same silence as unknown commands; button presses get a refusal.
denied = Router(name="admin_denied")


@denied.callback_query(F.data.startswith(("adm:", "sup:a:")))
async def adm_denied(cb: CallbackQuery) -> None:
    await cb.answer("Недостаточно прав", show_alert=True)


@denied.message(Command("admin", "ticket"))
async def adm_denied_cmd(message: Message) -> None:
    await message.answer(ui.warn("Недостаточно прав."))

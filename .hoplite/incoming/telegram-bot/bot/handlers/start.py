"""/start, main menu, subscription, trial, support."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from backend import webapp_url
from backend.models import User
from backend.services import account as account_svc
from backend.services import roles
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services import subscriptions as subs_svc
from backend.services import trial as trial_svc
from backend.services.context import AppContext
from bot import ui
from bot.common import BRAND, PLAN_NAMES, show, sub_line
from bot.keyboards import DevCb, Nav, TrialCb, back_row, is_https, main_menu, only_back
from bot.middlewares import user_locks

router = Router(name="start")


async def menu_text(ctx: AppContext, user_id: int) -> str:
    async with ctx.session() as session:
        sub = await subs_svc.get_current(session, user_id)
        used = await dev_svc.occupied_slots(session, sub.id) if sub else None
    return ui.screen(BRAND, ui.card(sub_line(sub, used)), ui.note("Выберите раздел ниже"))


async def _menu(ctx: AppContext, user_id: int):
    return main_menu(webapp_url.current(ctx.settings), is_admin=await roles.is_admin(ctx, user_id))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, ctx: AppContext) -> None:
    u = message.from_user
    async with ctx.session() as session, session.begin():
        reg = await ref_svc.register_user(
            session, ref_svc.TgProfile(u.id, u.username, u.first_name, u.last_name, u.language_code), command.args
        )
    from backend.services import access
    from bot.handlers.gate import send_channel, send_welcome

    st = await access.gate_state(ctx, u.id, force_channel=True)
    if not st.terms_accepted:
        await send_welcome(message, ctx, updated=not reg.created and reg.user.terms_version is not None)
        return
    if not st.allowed:
        await send_channel(message, ctx, st)
        return
    from bot.handlers.gate import send_main_menu

    name = ui.safe(u.first_name or "друг")
    hello = f"✨ <b>Добро пожаловать, {name}!</b>" if reg.created else f"✨ <b>С возвращением, {name}!</b>"
    intro = "Здесь вы получите VPN-подписку, подключите свои устройства и найдёте пошаговые инструкции."
    if reg.created and reg.referral_note == "ok":
        intro += "\n🤝 Вы пришли по приглашению друга — для вас доступен пробный период."
    elif reg.created and reg.referral_note == "self":
        intro += "\n△ Нельзя пригласить самого себя."
    await send_main_menu(message, ctx, u.id, greeting=f"{hello}\n{intro}")


@router.message(Command("menu"))
async def cmd_menu(message: Message, ctx: AppContext) -> None:
    from bot.handlers.gate import send_main_menu

    await send_main_menu(message, ctx, message.from_user.id)


@router.callback_query(Nav.filter(F.to == "menu"))
async def nav_menu(cb: CallbackQuery, ctx: AppContext, state=None) -> None:
    from bot.handlers.gate import send_main_menu

    if state is not None:
        await state.clear()
    try:
        await cb.answer()
    except Exception:  # noqa: BLE001 - already answered
        pass
    if cb.message is not None:
        await send_main_menu(cb.message, ctx, cb.from_user.id, source=cb.message)


@router.callback_query(Nav.filter(F.to == "sub"))
async def nav_sub(cb: CallbackQuery, ctx: AppContext) -> None:
    data = await account_svc.overview(ctx, cb.from_user.id)
    sub = data["subscription"]
    used = sum(1 for d in data["devices"] if d.status in ("pending", "active", "expired", "suspended", "revoking"))
    rows = []
    head = ui.title("Моя подписка")
    if sub is None:
        text = ui.screen(head, ui.no_subscription("Оформите тариф или попробуйте бесплатный пробный период — это займёт минуту."))
        if data["trial"].available:
            rows.append([InlineKeyboardButton(text=f"🎁 Пробный период · {data['trial'].days} дн.", callback_data=Nav(to="trial").pack())])
    else:
        name = PLAN_NAMES.get(sub.plan_code, sub.plan_code)
        text = ui.screen(head, ui.card(ui.kv("Тариф", f"<b>{name}</b>"), sub_line(sub, used)))
        if sub.is_lifetime:
            text += "\n\n" + ui.note("Бессрочный тариф не требует продления.")
        rows.append([InlineKeyboardButton(text="📱 Мои устройства", callback_data=Nav(to="devices").pack())])
    if data["bonus_balance"] > 0 and not (sub and sub.is_lifetime):
        text += "\n\n" + ui.kv("Бонусных дней", f"<b>{data['bonus_balance']}</b>")
        rows.append([InlineKeyboardButton(text="🎁 Применить бонусные дни", callback_data=Nav(to="bonus_apply").pack())])
    if not (sub and sub.is_lifetime):
        rows.append([InlineKeyboardButton(text="💎 Продлить или купить", callback_data=Nav(to="plans").pack())])
    rows.append(ui.nav_row())
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(Nav.filter(F.to == "bonus_apply"))
async def nav_bonus_apply(cb: CallbackQuery, ctx: AppContext) -> None:
    async with user_locks.get(cb.from_user.id):
        sub = await account_svc.apply_bonus_days(ctx, cb.from_user.id)
    await show(cb, ui.screen(ui.ok("Бонусные дни применены"), ui.card(sub_line(sub))), InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("sub", "‹ Подписка")]))


@router.callback_query(Nav.filter(F.to == "trial"))
async def nav_trial(cb: CallbackQuery, ctx: AppContext) -> None:
    from backend.services import trial_codes as tc_svc
    from bot.handlers.trial_code import code_button

    info = await trial_svc.trial_info(ctx, cb.from_user.id)
    code_rows = [[code_button()]] if (await tc_svc.info(ctx, cb.from_user.id)).available else []
    if not info.available:
        await show(cb, ui.screen(ui.title("Пробный период"), ui.card(ui.safe(info.reason))), InlineKeyboardMarkup(inline_keyboard=[*code_rows, ui.nav_row()]))
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✓ Активировать", callback_data=TrialCb(action="go").pack())], *code_rows, ui.nav_row()]
    )
    await show(cb, ui.screen(ui.title(f"Пробный период · {info.days} дн.", "Бесплатно, без карты"), ui.card("Доступ для одного устройства", "Активировать можно один раз"), ui.note("Нажмите «Активировать» — подключение создаётся на сервере.")), kb)


@router.callback_query(TrialCb.filter(F.action == "go"))
async def trial_go(cb: CallbackQuery, ctx: AppContext) -> None:
    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Уже активируем…")
        return
    async with lock:
        await cb.answer("Создаём подключение на сервере…")
        res = await trial_svc.activate_trial(ctx, cb.from_user.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚡ Получить ссылку и QR", callback_data=DevCb(action="link", id=res.device.id).pack())], ui.nav_row()]
    )
    await show(cb, ui.screen(ui.ok("Пробный период активирован"), ui.card("Сервер подтвердил создание подключения."), ui.note("Откройте ссылку или QR, чтобы подключить устройство.")), kb)


@router.message(Command("whoami"))
async def cmd_whoami(message: Message, ctx: AppContext) -> None:
    async with ctx.session() as session:
        user = await session.get(User, message.from_user.id)
    role = await roles.get_role(ctx, message.from_user.id)
    role_text = {"owner": "главный администратор", "admin": "администратор"}.get(role or "", "пользователь")
    await message.answer(
        ui.screen(ui.title("Ваш аккаунт"), ui.card(ui.kv("SpirittVPN ID", f"<code>{user.public_id}</code>"), ui.kv("Telegram ID", f"<code>{user.id}</code>"), ui.kv("Роль", role_text)))
    )


@router.message(Command("setup"))
async def cmd_setup(message: Message, command: CommandObject, ctx: AppContext) -> None:
    """One-time owner bootstrap; the code exists only in the server log."""
    await roles.claim_owner(ctx, message.from_user.id, command.args or "", ctx.extra.get("setup_code"))
    ctx.extra.pop("setup_code", None)
    await message.answer(ui.screen(ui.ok("Вы назначены главным администратором"), "Рекомендуем также указать OWNER_TELEGRAM_ID в .env. Откройте /admin."))

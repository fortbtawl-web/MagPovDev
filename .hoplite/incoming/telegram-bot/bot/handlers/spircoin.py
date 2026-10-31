"""SpirCoin, tasks, subscription link and how-to-connect screens inside the regular chat."""
from __future__ import annotations

import html
import uuid

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from backend import webapp_url
from backend.models import Device, Plan
from backend.services import coins as coins_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services import tasks as tasks_svc
from backend.services.context import AppContext
from backend.utils import fmt_dt
from bot import ui
from bot.common import show
from bot.keyboards import Nav, copy_button
from bot.middlewares import user_locks

router = Router(name="spircoin")
STATUS = {"available": "🟢 Доступно", "pending": "🟡 Ожидает проверки", "rewarded": "✅ Выполнено · награда получена", "rejected": "🔴 Отклонено", "unavailable": "⚪ Временно недоступно"}


class TaskCb(CallbackData, prefix="task"):
    action: str  # claim
    id: int


class CoinCb(CallbackData, prefix="coin"):
    action: str  # confirm | pay
    code: str
    key: str = ""


class HowCb(CallbackData, prefix="how"):
    os: str


# ---------- balance ----------

@router.callback_query(Nav.filter(F.to == "coins"))
async def coins_home(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        bal = await coins_svc.balance(session, cb.from_user.id)
        items = await coins_svc.history(session, cb.from_user.id, limit=8)
    lines = [f"{'+' if t.amount > 0 else '−'}{abs(t.amount)} · {coins_svc.KIND_LABELS.get(t.kind, t.kind)} · {fmt_dt(t.created_at)}" for t in items]
    blocks = [
        ui.title("SpirCoin", "Монеты за задания — подписка целиком"),
        ui.card(ui.kv("Баланс", f"<b>{coins_svc.fmt_coins(bal)}</b>")),
    ]
    if lines:
        blocks.append(ui.kv("Последние операции", "") + "\n" + ui.card(*lines))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Задания", callback_data=Nav(to="tasks").pack()),
         InlineKeyboardButton(text="💎 Тарифы", callback_data=Nav(to="plans").pack())],
        ui.nav_row(),
    ])
    await show(cb, ui.screen(*blocks), kb)


# ---------- tasks ----------

async def _tasks_screen(ctx: AppContext, user_id: int, notice: str = "") -> tuple[str, InlineKeyboardMarkup]:
    items = await tasks_svc.list_for_user(ctx, user_id)
    async with ctx.session() as session:
        bal = await coins_svc.balance(session, user_id)
    blocks = [ui.title("Задания", "Выполняйте и получайте SpirCoin"), ui.card(ui.kv("Баланс", f"<b>{coins_svc.fmt_coins(bal)}</b>"))]
    if notice:
        blocks.append(notice)
    rows: list[list[InlineKeyboardButton]] = []
    base = webapp_url.current(ctx.settings)
    for t in items:
        how = "Награда — сразу после перехода по кнопке" if t["verify"] == "telegram" else "Награда — после проверки модератором"
        blocks.append(ui.card(
            f"<b>{html.escape(t['title'])}</b>",
            html.escape(t["description"]) if t["description"] else "",
            f"💰 +{coins_svc.fmt_coins(t['reward'])} · <i>{how}</i>" if t["status"] != "rewarded" else "",
            STATUS[t["status"]] + (f" — <i>{html.escape(t['note'])}</i>" if t["note"] and t["status"] != "pending" else ""),
        ))
        if t["status"] not in ("available", "rejected"):
            continue
        label = f"🔗 {t['title'][:26]} · +{t['reward']}"
        if t["verify"] == "telegram":
            # One button: the click is recorded on our server (reward once), then the user lands on the task page.
            go = tasks_svc.click_url(ctx, base, t["id"], user_id)
            if go:
                rows.append([InlineKeyboardButton(text=label, url=go)])
            else:  # no public HTTPS address: record the press in the bot, then hand out the link
                rows.append([InlineKeyboardButton(text=label, callback_data=TaskCb(action="go", id=t["id"]).pack())])
        else:
            rows.append([
                InlineKeyboardButton(text=f"↗ {t['title'][:24]}", url=t["url"]),
                InlineKeyboardButton(text="✓ На проверку", callback_data=TaskCb(action="claim", id=t["id"]).pack()),
            ])
    if not items:
        blocks.append(ui.card("○ Заданий пока нет — загляните позже"))
    rows.append(ui.nav_row("coins", "‹ SpirCoin"))
    return ui.screen(*blocks), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(Nav.filter(F.to == "tasks"))
async def tasks_home(cb: CallbackQuery, ctx: AppContext) -> None:
    text, kb = await _tasks_screen(ctx, cb.from_user.id)
    await show(cb, text, kb)


@router.callback_query(TaskCb.filter(F.action.in_({"claim", "go"})))
async def task_claim(cb: CallbackQuery, callback_data: TaskCb, ctx: AppContext) -> None:
    from backend.models import Task, TaskClaim

    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Обрабатываем…")
        return
    async with lock:
        async with ctx.session() as session:
            task = await session.get(Task, callback_data.id)
            before = await session.scalar(
                select(TaskClaim.status).where(TaskClaim.task_id == callback_data.id, TaskClaim.user_id == cb.from_user.id)
            )
        res = await tasks_svc.claim(ctx, cb.from_user.id, callback_data.id)
    if res.status == "rewarded" and before != "rewarded" and task is not None:
        rows = []
        if task.url:
            rows.append([InlineKeyboardButton(text="🔗 Открыть задание", url=task.url)])
        rows += [[InlineKeyboardButton(text="🎯 К заданиям", callback_data=Nav(to="tasks").pack())], ui.nav_row()]
        await show(cb, tasks_svc.reward_message(task.reward, res.balance, task.title), InlineKeyboardMarkup(inline_keyboard=rows))
        return
    msg = html.escape(res.message)
    notice = ui.ok(msg) if res.status == "rewarded" else ui.warn(msg) if res.status in ("not_subscribed", "rejected") else ui.note(msg)
    text, kb = await _tasks_screen(ctx, cb.from_user.id, notice)
    await show(cb, text, kb)


# ---------- buy for coins ----------

async def coin_plan_button(ctx: AppContext, plan: Plan) -> InlineKeyboardButton | None:
    async with ctx.session() as session:
        enabled = bool(await settings_svc.get(session, "coins_enabled"))
    if not enabled or not plan.price_coins:
        return None
    return InlineKeyboardButton(text=f"🪙 За {coins_svc.fmt_coins(plan.price_coins)}", callback_data=CoinCb(action="confirm", code=plan.code).pack())


@router.callback_query(CoinCb.filter(F.action == "confirm"))
async def coin_confirm(cb: CallbackQuery, callback_data: CoinCb, ctx: AppContext) -> None:
    async with ctx.session() as session:
        plan = await session.scalar(select(Plan).where(Plan.code == callback_data.code, Plan.is_active.is_(True)))
        bal = await coins_svc.balance(session, cb.from_user.id)
        sub = await subs_svc.get_current(session, cb.from_user.id)
    if plan is None or not plan.price_coins:
        await cb.answer("Этот тариф нельзя купить за SpirCoin", show_alert=True)
        return
    if sub is not None and sub.is_lifetime:
        await cb.answer("У вас уже бессрочная подписка", show_alert=True)
        return
    price = plan.price_coins
    if bal < price:
        text = ui.screen(
            ui.title("Недостаточно SpirCoin"),
            ui.card(ui.kv("Тариф", f"{ui.safe(plan.title)} · {coins_svc.fmt_coins(price)}"), ui.kv("На балансе", coins_svc.fmt_coins(bal)), ui.kv("Не хватает", f"<b>{coins_svc.fmt_coins(price - bal)}</b>")),
            ui.note("Выполните задания, чтобы заработать монеты."),
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 Задания", callback_data=Nav(to="tasks").pack())], ui.nav_row("plans", "‹ Тарифы")])
        await show(cb, text, kb)
        return
    when = "продлит текущую подписку" if sub is not None and subs_svc.is_usable(sub) else "активирует подписку сразу"
    # The key is fixed when the confirmation is shown: tapping "Pay" twice can never charge twice.
    key = uuid.uuid4().hex
    text = ui.screen(
        ui.title("Подтвердите покупку"),
        ui.card(ui.kv("Тариф", f"<b>{ui.safe(plan.title)}</b>"), ui.kv("Цена", f"<b>{coins_svc.fmt_coins(price)}</b>"), ui.kv("Баланс после", coins_svc.fmt_coins(bal - price))),
        ui.note(f"Покупка {when}. Монеты будут списаны один раз."),
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✓ Оплатить SpirCoin", callback_data=CoinCb(action="pay", code=plan.code, key=key).pack())],
        [InlineKeyboardButton(text="Отмена", callback_data=Nav(to="plans").pack())],
    ])
    await show(cb, text, kb)


@router.callback_query(CoinCb.filter(F.action == "pay"))
async def coin_pay(cb: CallbackQuery, callback_data: CoinCb, ctx: AppContext) -> None:
    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Уже оформляем…")
        return
    async with lock:
        await cb.answer("Оформляем подписку…")
        res = await coins_svc.purchase(ctx, cb.from_user.id, callback_data.code, callback_data.key)
    async with ctx.session() as session:
        sub = await subs_svc.get_current(session, cb.from_user.id)
    until = "бессрочно" if sub and sub.is_lifetime else f"до {fmt_dt(sub.expires_at)}" if sub and sub.expires_at else "—"
    vpn = "Ключ готов — откройте «Моя ссылка подписки»." if res.confirmed else "Ключ создаётся — сервер ответит в ближайшие минуты, повторим автоматически."
    text = ui.screen(
        ui.title("Покупка"),
        ui.ok("Подписка оформлена"),
        ui.card(ui.kv("Действует", f"<b>{until}</b>"), ui.kv("Баланс", coins_svc.fmt_coins(res.balance))),
        ui.warn("Повторный запрос — монеты не списаны второй раз.") if res.replayed else "",
        ui.note(vpn),
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Моя ссылка подписки", callback_data=Nav(to="mysub").pack())],
        [InlineKeyboardButton(text="Как подключиться", callback_data=Nav(to="howto").pack())],
        ui.nav_row(),
    ])
    await show(cb, text, kb)


# ---------- my subscription link ----------

@router.callback_query(Nav.filter(F.to == "mysub"))
async def my_sub(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        devices = (await session.execute(
            select(Device).where(Device.user_id == cb.from_user.id, Device.status.in_(("active", "expired", "suspended")), Device.subscription_url.is_not(None))
        )).scalars().all()
    devices = sorted(devices, key=lambda d: (d.node.country_code != "ZZ", d.status != "active", d.id))
    if not devices:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Пробный период", callback_data=Nav(to="trial").pack()),
             InlineKeyboardButton(text="💎 Тарифы", callback_data=Nav(to="plans").pack())],
            [InlineKeyboardButton(text="📱 Устройства", callback_data=Nav(to="devices").pack())],
            ui.nav_row(),
        ])
        await show(cb, ui.screen(ui.title("Ссылка подписки"), ui.no_subscription("Активируйте пробный период или оформите тариф — ваша личная ссылка появится здесь.")), kb)
        return
    d = devices[0]
    text = ui.screen(
        ui.title("Ссылка подписки", f"{d.node.flag} {ui.safe(d.node.name)}"),
        f"<code>{d.subscription_url}</code>",
        ui.note("Откройте ссылку — на странице выберите своё устройство и нажмите «Добавить в приложение». Или скопируйте её и вставьте в Happ / v2RayTun / Hiddify."),
    )
    rows = [[InlineKeyboardButton(text="Открыть страницу подписки", url=d.subscription_url)]]
    if len(d.subscription_url) <= 256:
        rows.append([copy_button("Скопировать ссылку", d.subscription_url)])
    rows += [[InlineKeyboardButton(text="Как подключиться", callback_data=Nav(to="howto").pack())], ui.nav_row()]
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


# ---------- how to connect ----------

APPS = {
    "happ": ("Happ", {"android": "https://play.google.com/store/apps/details?id=com.happproxy", "ios": "https://apps.apple.com/app/happ-proxy-utility/id6504287215", "windows": "https://www.happ.su/main", "macos": "https://apps.apple.com/app/happ-proxy-utility/id6504287215"}),
    "v2raytun": ("v2RayTun", {"android": "https://play.google.com/store/apps/details?id=com.v2raytun.android", "ios": "https://apps.apple.com/app/v2raytun/id6476628951"}),
    "v2rayng": ("v2rayNG", {"android": "https://github.com/2dust/v2rayNG/releases"}),
    "hiddify": ("Hiddify", {"android": "https://github.com/hiddify/hiddify-app/releases", "ios": "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532", "windows": "https://github.com/hiddify/hiddify-app/releases", "macos": "https://github.com/hiddify/hiddify-app/releases", "other": "https://github.com/hiddify/hiddify-app/releases"}),
}
OS_TITLES = {"android": "Android", "ios": "iPhone / iPad", "windows": "Windows", "macos": "macOS", "other": "Другие устройства"}
STEPS = ui.steps([
    "Нажмите «Моя ссылка подписки» в боте.",
    "Установите приложение из официального источника (кнопки ниже).",
    "Импорт: откройте ссылку подписки и нажмите «Добавить в приложение» или скопируйте ссылку → в приложении «+» → «Импорт из буфера обмена».",
    "Подтвердите добавление подписки, если приложение спросит.",
    "Выберите «Автовыбор» (Happ, Hiddify) или нужную страну и нажмите «Подключиться».",
    "Проверьте: откройте ipinfo.io — страна должна совпадать с выбранным сервером.",
])
NOTES = {
    "android": "Рекомендуем Happ. v2RayTun и v2rayNG тоже подходят; группа «Автовыбор» есть в Happ и Hiddify, в остальных — обычный список серверов.",
    "ios": "Рекомендуем Happ или v2RayTun из App Store. Автовыбор поддерживают Happ и Hiddify.",
    "windows": "Рекомендуем Happ для Windows. Hiddify — альтернатива с автовыбором.",
    "macos": "Рекомендуем Happ из App Store (Mac с Apple Silicon) или Hiddify.",
    "other": "Подойдёт любой клиент с поддержкой VLESS Reality и подписок: например Hiddify (Linux) или Happ на Android TV.",
}


@router.callback_query(Nav.filter(F.to == "howto"))
async def howto(cb: CallbackQuery) -> None:
    from bot.handlers.wizard import devices_screen

    await show(cb, *devices_screen())


async def howto_legacy(cb: CallbackQuery) -> None:
    """Previous OS-only screen; old messages still reach it through HowCb."""
    rows = [[InlineKeyboardButton(text=OS_TITLES["android"], callback_data=HowCb(os="android").pack()),
             InlineKeyboardButton(text=OS_TITLES["ios"], callback_data=HowCb(os="ios").pack())],
            [InlineKeyboardButton(text=OS_TITLES["windows"], callback_data=HowCb(os="windows").pack()),
             InlineKeyboardButton(text=OS_TITLES["macos"], callback_data=HowCb(os="macos").pack())],
            [InlineKeyboardButton(text=OS_TITLES["other"], callback_data=HowCb(os="other").pack())],
            ui.nav_row()]
    await show(cb, ui.screen(ui.title("Как подключиться", "Выберите устройство"), ui.note("Покажем пошаговую инструкцию и официальные ссылки на приложения.")), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(HowCb.filter())
async def howto_os(cb: CallbackQuery, callback_data: HowCb) -> None:
    os_ = callback_data.os if callback_data.os in OS_TITLES else "other"
    buttons = [InlineKeyboardButton(text=f"↓ {name}", url=links[os_]) for name, links in APPS.values() if os_ in links]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows += [[InlineKeyboardButton(text="⚡ Моя ссылка подписки", callback_data=Nav(to="mysub").pack())], ui.nav_row("howto", "‹ Инструкция")]
    await show(cb, ui.screen(ui.title(OS_TITLES[os_], "Подключение за несколько шагов"), STEPS, ui.card(NOTES[os_])), InlineKeyboardMarkup(inline_keyboard=rows))

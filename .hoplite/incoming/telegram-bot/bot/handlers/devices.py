"""Устройства: list, add, link + QR, rename, replace, revoke."""
from __future__ import annotations

import io

import qrcode
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from backend.models import Device, VpnNode
from backend.services import devices as dev_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_dt
from bot import ui
from bot.common import show
from bot.keyboards import DevCb, Nav, copy_button
from bot.middlewares import user_locks

router = Router(name="devices")
STATUS_ICON = {"pending": "🟡", "active": "🟢", "expired": "⚪", "suspended": "🟠", "revoking": "🟠", "revoked": "⚫", "failed": "🔴"}


class RenameFSM(StatesGroup):
    waiting = State()


def qr_png(data: str) -> bytes:
    img = qrcode.make(data, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _device(ctx: AppContext, user_id: int, device_id: int) -> Device:
    async with ctx.session() as session:
        d = await session.get(Device, device_id)
    if d is None or d.user_id != user_id:
        raise ServiceError("Устройство не найдено.", "not_found")
    return d


@router.callback_query(Nav.filter(F.to == "devices"))
async def devices_list(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    async with ctx.session() as session:
        sub = await subs_svc.get_current(session, cb.from_user.id)
        items = await dev_svc.list_devices(session, cb.from_user.id)
    used = sum(1 for d in items if d.status in ("pending", "active", "expired", "suspended", "revoking"))
    rows = [
        [InlineKeyboardButton(text=f"{STATUS_ICON.get(d.status, '●')} {d.label} · {d.node.flag} {d.node.name}", callback_data=DevCb(action="view", id=d.id).pack())]
        for d in items
    ]
    if sub is None:
        text = ui.screen(ui.title("Устройства"), ui.no_subscription("Устройства появятся, когда вы оформите тариф или активируете пробный период."))
        rows.append([InlineKeyboardButton(text="💎 Тарифы", callback_data=Nav(to="plans").pack())])
    else:
        blocks = [ui.title("Устройства"), ui.card(ui.kv("Занято", f"{used} из {sub.device_limit}"), "" if items else "○ Подключений пока нет")]
        if used < sub.device_limit and sub.status == "active":
            rows.append([InlineKeyboardButton(text="＋ Добавить устройство", callback_data=DevCb(action="add").pack())])
        elif sub.status != "active":
            blocks.append(ui.warn("Подписка не активна — новые устройства добавить нельзя."))
        text = ui.screen(*blocks)
    rows.append(ui.nav_row())
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(DevCb.filter(F.action == "add"))
async def device_add(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        nodes = (await session.execute(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))).scalars().all()
    nodes = sorted(nodes, key=lambda n: n.country_code != "ZZ")
    if not nodes:
        await cb.answer("VPN-серверы ещё не подключены", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=f"{n.flag} {n.name}{' · △ недоступна' if n.last_health_ok is False else ''}", callback_data=DevCb(action="addnode", node=n.code).pack())]
        for n in nodes
    ]
    rows.append(ui.nav_row("devices", "‹ Устройства"))
    await show(cb, ui.screen(ui.title("Новое устройство", "Выберите сервер"), ui.card(ui.kv("Все локации", "одна подписка со всеми странами; приложение само выберет самый быстрый сервер (Happ, Hiddify)"))), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(DevCb.filter(F.action == "addnode"))
async def device_add_node(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Уже создаём…")
        return
    async with lock:
        await cb.answer("Создаём подключение на сервере…")
        res = await dev_svc.add_device(ctx, cb.from_user.id, node_code=callback_data.node)
    if res.confirmed:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⚡ Ссылка и QR-код", callback_data=DevCb(action="link", id=res.device.id).pack())], ui.nav_row("devices", "‹ Устройства")]
        )
        await show(cb, ui.screen(ui.title("Новое устройство"), ui.ok(f"Устройство «{ui.safe(res.device.label)}» создано"), ui.note("Подтверждено сервером.")), kb)
    else:
        await show(
            cb,
            ui.screen(
                ui.title("Новое устройство"),
                ui.warn("Сервер не подтвердил создание подключения."),
                ui.note(ui.safe(res.error or "")),
                ui.note("Если сервер временно недоступен, мы повторим автоматически."),
            ),
            InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("devices", "‹ Устройства")]),
        )


@router.callback_query(DevCb.filter(F.action == "view"))
async def device_view(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    d = await _device(ctx, cb.from_user.id, callback_data.id)
    labels = {"pending": "создаётся на сервере", "active": "активно", "expired": "подписка истекла", "suspended": "приостановлено", "revoking": "отключается — ждём подтверждения сервера", "revoked": "отключено", "failed": "ошибка создания"}
    lines = [
        ui.kv("Статус", f"{STATUS_ICON.get(d.status, '')} {labels.get(d.status, d.status)}"),
        ui.kv("Сервер", f"{d.node.flag} {ui.safe(d.node.name)}"),
        ui.kv("Протокол", d.protocol.upper()),
        ui.kv("Создано", fmt_dt(d.created_at)),
    ]
    if d.node_expires_at and d.status in ("active", "expired"):
        lines.append(ui.kv("Действует до", fmt_dt(d.node_expires_at)))
    blocks = [ui.title(ui.safe(d.label)), ui.card(*lines)]
    if d.last_error and d.status in ("pending", "revoking"):
        blocks.append(ui.warn(ui.safe(d.last_error)))
    text = ui.screen(*blocks)
    rows = []
    if d.status in ("active", "expired", "suspended"):
        rows.append([InlineKeyboardButton(text="⚡ Ссылка и QR-код", callback_data=DevCb(action="link", id=d.id).pack())])
        rows.append([
            InlineKeyboardButton(text="Переименовать", callback_data=DevCb(action="rename", id=d.id).pack()),
            InlineKeyboardButton(text="Заменить", callback_data=DevCb(action="replace", id=d.id).pack()),
        ])
    if d.status not in ("revoked",):
        rows.append([InlineKeyboardButton(text="Отключить устройство", callback_data=DevCb(action="revoke", id=d.id).pack())])
    rows.append(ui.nav_row("devices", "‹ Устройства"))
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(DevCb.filter(F.action == "link"))
async def device_link(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    await cb.answer("Получаем ссылку с сервера…")
    device, live = await dev_svc.refresh_link(ctx, cb.from_user.id, callback_data.id)
    sub_url, vless = device.subscription_url, device.connection_link
    all_locations = device.node.country_code == "ZZ"
    if all_locations:
        vless = None  # the stored key belongs to one country only; the subscription carries every location
    link = sub_url or vless  # the QR carries the subscription when there is one
    if not link:
        await cb.message.answer(ui.warn("Сервер не вернул ссылку подключения. Попробуйте позже."))
        return
    note = "" if live else "\n\n" + ui.warn("Сервер сейчас недоступен — показаны сохранённые данные.")
    rows = []
    if sub_url:
        rows.append([copy_button("Скопировать ссылку", sub_url)])
        if sub_url.startswith("https://"):
            rows.append([InlineKeyboardButton(text="Открыть страницу подписки", url=sub_url)])
    # Telegram copy buttons are capped at 256 chars; a longer value would be silently truncated and broken.
    if vless and len(vless) <= 256:
        rows.append([copy_button("Скопировать ключ vless://", vless)])
    rows.append(ui.nav_row("devices", "‹ Устройства"))
    parts = [ui.title(ui.safe(device.label), f"{device.node.flag} {ui.safe(device.node.name)}")]
    if sub_url:
        parts.append(ui.kv("Подписка", "обновляется сама, рекомендуем") + f"\n<code>{sub_url}</code>")
    if vless:
        parts.append(ui.kv("Ключ vless://", "нажмите, чтобы скопировать") + f"\n<code>{vless}</code>")
    howto = "\n\n" + ui.note("Отсканируйте QR-код или импортируйте в Happ, v2RayTun, Hiddify, v2rayNG или Streisand.")
    if all_locations:
        howto += "\n" + ui.note("В Happ и Hiddify выберите группу «Автовыбор» — приложение само берёт сервер с лучшим пингом. Конкретную страну можно выбрать вручную.")
    caption = "\n\n".join(parts) + howto + note
    if len(caption) > 1024:  # photo captions are capped at 1024 chars
        await cb.message.answer_photo(
            BufferedInputFile(qr_png(link), filename="spirittvpn.png"),
            caption=parts[0] + "\n\n" + ui.note("QR-код — для импорта в приложение. Ссылки — ниже."),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await cb.message.answer("\n\n".join(parts[1:]) + note)
        return
    await cb.message.answer_photo(
        BufferedInputFile(qr_png(link), filename="spirittvpn.png"), caption=caption, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(DevCb.filter(F.action == "rename"))
async def device_rename(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext, state: FSMContext) -> None:
    await _device(ctx, cb.from_user.id, callback_data.id)
    await state.set_state(RenameFSM.waiting)
    await state.update_data(device_id=callback_data.id)
    await show(cb, ui.screen(ui.title("Переименование"), "Отправьте новое название устройства (до 48 символов)."), InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=Nav(to="devices").pack())]]))


@router.message(StateFilter(RenameFSM.waiting), F.text)
async def device_rename_value(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    d = await dev_svc.rename_device(ctx, message.from_user.id, int(data["device_id"]), message.text)
    await message.answer(ui.ok(f"Новое название: «{ui.safe(d.label)}»"), reply_markup=InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("devices", "‹ Устройства")]))


@router.callback_query(DevCb.filter(F.action == "revoke"))
async def device_revoke(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    d = await _device(ctx, cb.from_user.id, callback_data.id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, отключить", callback_data=DevCb(action="revoke_ok", id=d.id).pack())],
            [InlineKeyboardButton(text="Отмена", callback_data=DevCb(action="view", id=d.id).pack())],
        ]
    )
    await show(cb, ui.screen(ui.title("Отключить устройство?"), ui.card(ui.kv("Устройство", ui.safe(d.label))), ui.warn("Ключ перестанет работать сразу после подтверждения сервера. Вернуть его будет нельзя — потребуется новое устройство.")), kb)


@router.callback_query(DevCb.filter(F.action == "revoke_ok"))
async def device_revoke_ok(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    async with user_locks.get(cb.from_user.id):
        res = await dev_svc.revoke_device(ctx, callback_data.id, user_id=cb.from_user.id)
    text = ui.screen(ui.title("Отключение"), ui.ok("Устройство отключено"), ui.note("Сервер подтвердил отзыв ключа.")) if res.confirmed else ui.screen(
        ui.title("Отключение"),
        ui.warn("Сервер пока не подтвердил отключение. Устройство в статусе «отключается» — мы повторим автоматически."),
    )
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("devices", "‹ Устройства")]))


@router.callback_query(DevCb.filter(F.action == "replace"))
async def device_replace(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    d = await _device(ctx, cb.from_user.id, callback_data.id)
    async with ctx.session() as session:
        nodes = (await session.execute(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))).scalars().all()
    nodes = sorted(nodes, key=lambda n: n.country_code != "ZZ")
    rows = [[InlineKeyboardButton(text=f"{n.flag} {n.name}{' · △ недоступна' if n.last_health_ok is False else ''}", callback_data=DevCb(action="replace_node", id=d.id, node=n.code).pack())] for n in nodes]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=DevCb(action="view", id=d.id).pack())])
    await show(cb, ui.screen(ui.title("Замена устройства", "Выберите сервер"), ui.card(ui.kv("Устройство", ui.safe(d.label))), ui.warn("Старый ключ будет отключён, новый — создан на выбранном сервере.")), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(DevCb.filter(F.action == "replace_node"))
async def device_replace_node(cb: CallbackQuery, callback_data: DevCb, ctx: AppContext) -> None:
    lock = user_locks.get(cb.from_user.id)
    if lock.locked():
        await cb.answer("Уже выполняем…")
        return
    async with lock:
        await cb.answer("Заменяем…")
        res = await dev_svc.replace_device(ctx, cb.from_user.id, callback_data.id, node_code=callback_data.node)
    if res.confirmed:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚡ Новая ссылка и QR", callback_data=DevCb(action="link", id=res.device.id).pack())], ui.nav_row("devices", "‹ Устройства")])
        await show(cb, ui.screen(ui.title("Замена устройства"), ui.ok("Устройство заменено"), ui.note("Старый ключ отключён.")), kb)
    else:
        await show(cb, ui.screen(ui.title("Замена устройства"), ui.warn("Старый ключ отключён, но новый сервер не подтвердил создание."), ui.note(ui.safe(res.error or "")), ui.note("Мы повторим автоматически.")), InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("devices", "‹ Устройства")]))

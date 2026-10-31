"""Поддержка: tickets inside the bot (text, photos and documents)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.services import support as support_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import fmt_dt
from bot import ui
from bot.common import show
from bot.keyboards import Nav

router = Router(name="support")
STATUS_ICON = {"new": "🟡", "in_progress": "🟠", "waiting_user": "🟡", "resolved": "🟢", "closed": "⚪"}


class SupportFSM(StatesGroup):
    text = State()
    reply = State()


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract(message: Message) -> tuple[str, tuple[str, str] | None]:
    """Text plus an optional Telegram attachment (photo/document file_id)."""
    if message.photo:
        return (message.caption or "Фото"), (message.photo[-1].file_id, "photo")
    if message.document:
        return (message.caption or message.document.file_name or "Файл"), (message.document.file_id, "document")
    return (message.text or ""), None


def home_kb(ctx: AppContext) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Новое обращение", callback_data="sup:new")],
        [InlineKeyboardButton(text="Мои обращения", callback_data="sup:list")],
    ]
    if ctx.settings.support_url:
        rows.append([InlineKeyboardButton(text="💬 Написать оператору", url=ctx.settings.support_url)])
    rows.append(ui.nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


HOME_TEXT = ui.screen(
    ui.title("Поддержка", "Ответим здесь, в чате с ботом"),
    ui.card(
        ui.kv("Как подключиться", "«Устройства» → устройство → «Ссылка и QR», затем импорт в Happ, v2RayTun, Hiddify или Streisand"),
        ui.kv("Оплата", "доступ выдаётся после подтверждения платёжной системой"),
    ),
    ui.note("Не нашли ответ? Создайте обращение — к нему можно приложить скриншот."),
)


async def _home_text(ctx: AppContext, user_id: int) -> str:
    used, limit = await support_svc.daily_usage(ctx, user_id)
    return ui.screen(HOME_TEXT, ui.card(ui.kv("Обращений за сутки", f"<b>{min(used, limit)} из {limit}</b>")))


@router.callback_query(Nav.filter(F.to == "support"))
async def support_home(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await show(cb, await _home_text(ctx, cb.from_user.id), home_kb(ctx))


@router.message(Command("support"))
async def cmd_support(message: Message, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await _home_text(ctx, message.from_user.id), reply_markup=home_kb(ctx))


@router.callback_query(F.data == "sup:new")
async def support_new(cb: CallbackQuery, ctx: AppContext) -> None:
    used, limit = await support_svc.daily_usage(ctx, cb.from_user.id)
    if used >= limit:
        await cb.answer(f"Лимит обращений: {limit} в сутки. Попробуйте позже или дополните открытое обращение.", show_alert=True)
        return
    items = list(support_svc.CATEGORIES.items())
    rows = [[InlineKeyboardButton(text=label, callback_data=f"sup:cat:{key}") for key, label in items[i : i + 2]] for i in range(0, len(items), 2)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=Nav(to="support").pack())])
    await show(cb, ui.screen(ui.title("Новое обращение", "Выберите категорию")), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sup:cat:"))
async def support_cat(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":", 2)[2]
    if key not in support_svc.CATEGORIES:
        await cb.answer("Неизвестная категория", show_alert=True)
        return
    await state.set_state(SupportFSM.text)
    await state.update_data(category=key)
    await show(
        cb,
        ui.screen(
            ui.title("Новое обращение"),
            ui.card(ui.kv("Категория", ui.safe(support_svc.CATEGORIES[key]))),
            "Опишите проблему одним сообщением.",
            ui.note("Можно прислать скриншот или файл с подписью."),
        ),
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=Nav(to="support").pack())]]),
    )


@router.message(StateFilter(SupportFSM.text), F.text | F.photo | F.document)
async def support_text(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    text, attachment = extract(message)
    ticket, _ = await support_svc.create_ticket(ctx, message.from_user.id, data["category"], text, attachment=attachment)
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть обращение", callback_data=f"sup:v:{ticket.id}")], ui.nav_row("support", "‹ Поддержка")])
    await message.answer(ui.screen(ui.title("Поддержка"), ui.ok(f"Обращение {ui.safe(ticket.number)} создано"), ui.note("Мы ответим здесь, в чате с ботом.")), reply_markup=kb)


@router.callback_query(F.data == "sup:list")
async def support_list(cb: CallbackQuery, ctx: AppContext) -> None:
    rows_data = await support_svc.user_tickets(ctx, cb.from_user.id)
    if not rows_data:
        await show(cb, ui.screen(ui.title("Мои обращения"), ui.card("○ Обращений пока нет")), home_kb(ctx))
        return
    rows = [
        [InlineKeyboardButton(text=f"{STATUS_ICON.get(t.status, '●')} {t.number} · {t.subject[:30]}", callback_data=f"sup:v:{t.id}")]
        for t, _ in rows_data[:15]
    ]
    rows.append(ui.nav_row("support", "‹ Поддержка"))
    await show(cb, ui.screen(ui.title("Мои обращения", "Выберите, чтобы открыть")), InlineKeyboardMarkup(inline_keyboard=rows))


def thread_text(ticket, msgs, *, admin: bool = False) -> str:
    lines = [
        ui.title(f"{ui.safe(ticket.number)}", ui.safe(support_svc.CATEGORIES.get(ticket.category, ticket.category))),
        ui.card(ui.kv("Статус", f"{STATUS_ICON.get(ticket.status, '')} {ui.safe(support_svc.STATUS_LABELS.get(ticket.status, ticket.status))}")),
    ]
    for m in msgs[-12:]:
        who = {"user": "Вы" if not admin else "Пользователь", "admin": "Поддержка", "system": "Система"}[m.author_type]
        if admin and m.author_type == "admin":
            who = f"Админ {m.author_id}"
        mark = "[внутр.] " if m.is_internal else ""
        att = " · вложение" if m.attachment_file_id else ""
        lines.append(f"{mark}<b>{who}</b> · {fmt_dt(m.created_at)}{att}\n{esc(m.body[:600])}")
    text = "\n\n".join(lines)
    return text[-3900:]


@router.callback_query(F.data.startswith("sup:v:"))
async def support_view(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    await state.clear()
    ticket_id = int(cb.data.split(":")[2])
    ticket, msgs = await support_svc.user_thread(ctx, cb.from_user.id, ticket_id)
    rows = []
    if ticket.status != "closed":
        rows.append([InlineKeyboardButton(text="Ответить", callback_data=f"sup:r:{ticket.id}"), InlineKeyboardButton(text="Закрыть обращение", callback_data=f"sup:close:{ticket.id}")])
    atts = [m for m in msgs if m.attachment_file_id]
    if atts:
        rows.append([InlineKeyboardButton(text=f"Вложения ({len(atts)})", callback_data=f"sup:att:{ticket.id}")])
    rows.append([InlineKeyboardButton(text="‹ Мои обращения", callback_data="sup:list")])
    rows.append(ui.nav_row())
    await show(cb, thread_text(ticket, msgs), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sup:att:"))
async def support_attachments(cb: CallbackQuery, ctx: AppContext) -> None:
    ticket_id = int(cb.data.split(":")[2])
    _, msgs = await support_svc.user_thread(ctx, cb.from_user.id, ticket_id)
    await cb.answer()
    for m in [m for m in msgs if m.attachment_file_id][-5:]:
        if m.attachment_kind == "photo":
            await cb.message.answer_photo(m.attachment_file_id)
        else:
            await cb.message.answer_document(m.attachment_file_id)


@router.callback_query(F.data.startswith("sup:r:"))
async def support_reply(cb: CallbackQuery, ctx: AppContext, state: FSMContext) -> None:
    ticket_id = int(cb.data.split(":")[2])
    ticket, _ = await support_svc.user_thread(ctx, cb.from_user.id, ticket_id)  # ownership check
    if ticket.status == "closed":
        await cb.answer("Обращение закрыто", show_alert=True)
        return
    await state.set_state(SupportFSM.reply)
    await state.update_data(ticket_id=ticket_id)
    await show(
        cb,
        ui.screen(ui.title("Ответ в обращение", ui.safe(ticket.number)), "Отправьте текст, фото или файл."),
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"sup:v:{ticket_id}")]]),
    )


@router.message(StateFilter(SupportFSM.reply), F.text | F.photo | F.document)
async def support_reply_value(message: Message, ctx: AppContext, state: FSMContext) -> None:
    data = await state.get_data()
    text, attachment = extract(message)
    ticket, _ = await support_svc.user_message(ctx, message.from_user.id, int(data["ticket_id"]), text, attachment=attachment)
    await state.clear()
    await message.answer(
        ui.ok(f"Сообщение добавлено в {ui.safe(ticket.number)}"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть обращение", callback_data=f"sup:v:{ticket.id}")], ui.nav_row("support", "‹ Поддержка")]),
    )


@router.callback_query(F.data.startswith("sup:close:"))
async def support_close(cb: CallbackQuery, ctx: AppContext) -> None:
    ticket = await support_svc.user_close(ctx, cb.from_user.id, int(cb.data.split(":")[2]))
    await show(cb, ui.screen(ui.title("Поддержка"), ui.ok(f"Обращение {ui.safe(ticket.number)} закрыто"), ui.note("Спасибо! Если вопрос останется — создайте новое обращение.")), home_kb(ctx))


__all__ = ["router", "SupportFSM", "ServiceError", "thread_text", "esc"]

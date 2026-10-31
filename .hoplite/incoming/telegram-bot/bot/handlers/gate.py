"""Access gate screens: welcome with logo, mandatory documents, mandatory channel subscription."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from backend.services import access
from bot import ui
from bot.handlers.wizard import send_offer
from backend.services.access import GateState
from backend.services.context import AppContext

log = logging.getLogger("spiritt.bot.gate")
router = Router(name="gate")
ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "assets" / "logo.png"
WELCOME_IMAGE = ROOT / "assets" / "welcome.png"
LINE = "━━━━━━━━━━━━━━━━"


def _flag(cc: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in cc.upper()) if len(cc) == 2 and cc.isalpha() else ""


def locations_line(ctx: AppContext) -> str:
    """Real locations from VPN_NODES (the virtual "all locations" node is skipped)."""
    try:
        specs = [sp for sp in ctx.settings.node_specs() if sp.code != "all"]
    except ValueError:
        return ""
    return " · ".join(f"{_flag(sp.country_code)} {ui.safe(sp.name)}".strip() for sp in specs)


def brand_header(ctx: AppContext) -> str:
    where = locations_line(ctx)
    return "\n".join(line for line in (
        "🌐 <b>SPIRITTVPN — ТВОЯ СВОБОДА В СЕТИ</b>",
        LINE,
        "🔒 Защищённое соединение на протоколе VLESS Reality",
        "⚡ Подключение за пару минут — с пошаговой инструкцией",
        f"🌍 {where}" if where else "",
        LINE,
    ) if line)


def welcome_text(ctx: AppContext) -> str:
    return "\n\n".join((
        brand_header(ctx),
        "📄 <b>Шаг 1 из 2 — условия использования</b>\nОзнакомьтесь с документами и подтвердите согласие — без этого сервис недоступен.",
    ))


def updated_text(ctx: AppContext) -> str:
    """Shown to everyone after TERMS_VERSION changes: same look as the welcome card, with the reason."""
    return "\n\n".join((
        brand_header(ctx),
        "✨ <b>SpirittVPN обновился</b>\nМы обновили оформление и документы сервиса.",
        "📄 <b>Шаг 1 из 2 — условия использования</b>\nОзнакомьтесь с документами и подтвердите согласие, затем подтвердите подписку на наш канал — после этого всё откроется снова.",
    ))


CHANNEL_TEXT = ui.screen(
    ui.title("Остался один шаг", "Шаг 2 из 2 · подписка на канал"),
    "📢 В нашем канале — новости сервиса, статус серверов и важные объявления.",
    ui.steps(["Подпишитесь на канал", "Вернитесь в бота", "Нажмите «Проверить подписку»"]),
)


def terms_kb(ctx: AppContext) -> InlineKeyboardMarkup:
    s = ctx.settings
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Политика конфиденциальности", url=s.privacy_url)],
            [InlineKeyboardButton(text="📑 Пользовательское соглашение", url=s.terms_url)],
            [InlineKeyboardButton(text="✓ Принимаю условия", callback_data="gate:accept")],
        ]
    )


def channel_kb(ctx: AppContext) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=ctx.settings.required_channel_url)],
            [InlineKeyboardButton(text="✓ Проверить подписку", callback_data="gate:check")],
        ]
    )


async def send_picture(message: Message, ctx: AppContext, caption: str, markup: InlineKeyboardMarkup) -> Message:
    """Welcome image with the caption as one block; plain text if the image is missing or rejected."""
    file_id = ctx.extra.get("welcome_file_id")
    image = WELCOME_IMAGE if WELCOME_IMAGE.is_file() else LOGO
    if file_id or image.is_file():
        try:
            sent = await message.answer_photo(file_id or FSInputFile(image), caption=caption, reply_markup=markup)
            if sent.photo:
                ctx.extra["welcome_file_id"] = sent.photo[-1].file_id  # uploaded once, then reused by id
            return sent
        except (TelegramAPIError, OSError) as exc:
            log.warning("welcome image not sent (%s), falling back to text", type(exc).__name__)
            ctx.extra.pop("welcome_file_id", None)
    else:
        log.warning("welcome image %s is missing", image)
    try:
        return await message.answer(caption, reply_markup=markup, disable_web_page_preview=True)
    except TelegramForbiddenError:
        log.info("chat %s unavailable (blocked/deactivated), menu not sent", message.chat.id)
        return message


async def replace_previous_welcome(message: Message, ctx: AppContext, user_id: int) -> None:
    """Removes the previous welcome/menu photo so repeated /start or «Главное меню» never stack copies."""
    from bot.common import MENU_PHOTOS

    old = MENU_PHOTOS.pop(message.chat.id, None)
    if old:
        try:
            await message.bot.delete_message(message.chat.id, old)
        except TelegramAPIError:
            pass  # older than 48 h or already deleted


def remember_welcome(ctx: AppContext, user_id: int, sent: Message) -> None:
    from bot.common import MENU_PHOTOS

    if sent.photo:
        MENU_PHOTOS[sent.chat.id] = sent.message_id
        if len(MENU_PHOTOS) > 50_000:
            MENU_PHOTOS.clear()


async def menu_caption(ctx: AppContext, user_id: int, greeting: str = "") -> str:
    from backend.services import devices as dev_svc
    from backend.services import subscriptions as subs_svc
    from bot.common import sub_line

    async with ctx.session() as session:
        sub = await subs_svc.get_current(session, user_id)
        used = await dev_svc.occupied_slots(session, sub.id) if sub else None
    parts = [brand_header(ctx)]
    if greeting:
        parts.append(greeting)
    parts += [ui.card(sub_line(sub, used)), "Выберите раздел ниже 👇"]
    return "\n\n".join(parts)


async def send_main_menu(message: Message, ctx: AppContext, user_id: int, *, greeting: str = "", source: Message | None = None) -> None:
    """Main menu = welcome image + caption + menu buttons.

    From the menu photo itself (e.g. refresh) the caption is edited in place; from any other screen that screen's
    message is removed and a fresh menu photo is sent, so the chat keeps a single current menu.
    """
    from bot.common import MENU_PHOTOS
    from bot.handlers.start import _menu

    caption = await menu_caption(ctx, user_id, greeting)
    markup = await _menu(ctx, user_id)
    if source is not None and source.photo and MENU_PHOTOS.get(source.chat.id) == source.message_id:
        try:
            await source.edit_caption(caption=caption, reply_markup=markup)
            return
        except TelegramAPIError as exc:
            if "not modified" in str(exc):
                return
    await replace_previous_welcome(message, ctx, user_id)
    sent = await send_picture(message, ctx, caption, markup)
    remember_welcome(ctx, user_id, sent)
    if source is not None and source.message_id != sent.message_id:
        try:
            await source.delete()
        except TelegramAPIError:
            pass


async def send_welcome(message: Message, ctx: AppContext, *, updated: bool = False) -> None:
    text = welcome_text(ctx) if not updated else updated_text(ctx)
    user_id = message.chat.id
    await replace_previous_welcome(message, ctx, user_id)
    remember_welcome(ctx, user_id, await send_picture(message, ctx, text, terms_kb(ctx)))


async def send_channel(target: Message, ctx: AppContext, st: GateState, *, note: str | None = None) -> None:
    text = CHANNEL_TEXT
    if st.channel_error:
        text += f"\n\n{ui.warn(st.channel_error)}"
    elif note:
        text += f"\n\n{note}"
    await target.answer(text, reply_markup=channel_kb(ctx), disable_web_page_preview=True)


async def show_gate(event: Message | CallbackQuery, ctx: AppContext, st: GateState) -> None:
    """Called by the middleware when a protected action is attempted without access."""
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer("Сначала завершите шаги доступа", show_alert=False)
    if message is None:
        return
    if not st.terms_accepted:
        async with ctx.session() as session:
            from backend.models import User

            had_old = (await session.get(User, event.from_user.id)).terms_version is not None
        await send_welcome(message, ctx, updated=had_old)
    else:
        await send_channel(message, ctx, st)


async def open_menu(message: Message, ctx: AppContext, user_id: int) -> None:
    await send_main_menu(message, ctx, user_id)


@router.callback_query(F.data == "gate:accept")
async def gate_accept(cb: CallbackQuery, ctx: AppContext) -> None:
    await access.accept_terms(ctx, cb.from_user.id, ctx.settings.terms_version, "bot")
    await cb.answer("Условия приняты")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    st = await access.gate_state(ctx, cb.from_user.id, force_channel=True)
    if st.allowed:
        await send_offer(cb.message)
    else:
        await send_channel(cb.message, ctx, st)


@router.callback_query(F.data == "gate:check")
async def gate_check(cb: CallbackQuery, ctx: AppContext) -> None:
    st = await access.gate_state(ctx, cb.from_user.id, force_channel=True)
    if not st.terms_accepted:
        await cb.answer()
        await send_welcome(cb.message, ctx)
        return
    if st.allowed:
        await cb.answer("Подписка подтверждена")
        try:
            await cb.message.delete()
        except TelegramAPIError:
            pass
        await send_offer(cb.message)
        return
    if st.channel_error:
        await cb.answer("Не удалось проверить — попробуйте позже", show_alert=True)
    else:
        await cb.answer("Подписка не найдена. Подпишитесь на канал и нажмите «Проверить» ещё раз.", show_alert=True)

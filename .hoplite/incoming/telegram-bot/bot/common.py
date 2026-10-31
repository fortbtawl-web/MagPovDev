"""Shared helpers for handlers: safe message editing and common texts."""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from backend.models import Subscription
from backend.utils import fmt_dt

from bot.ui import BRAND, STATUS  # noqa: E402,F401  (re-exported for handlers)


# chat id -> message id of the current main-menu photo (one per chat, so the chat stays tidy)
MENU_PHOTOS: dict[int, int] = {}


async def show(event: CallbackQuery | Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the callback's message when possible (stale/photo messages get a fresh one)."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message) and msg.text is None and MENU_PHOTOS.get(msg.chat.id) == msg.message_id:
            # Leaving the main menu: the section opens as text and the menu photo is removed, not duplicated.
            await msg.answer(text, reply_markup=markup, disable_web_page_preview=True)
            MENU_PHOTOS.pop(msg.chat.id, None)
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
            try:
                await event.answer()
            except TelegramBadRequest:
                pass
            return
        if isinstance(msg, Message) and msg.text is not None:
            try:
                await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc):
                    await msg.answer(text, reply_markup=markup, disable_web_page_preview=True)
        elif isinstance(msg, Message):
            await msg.answer(text, reply_markup=markup, disable_web_page_preview=True)
        try:
            await event.answer()
        except TelegramBadRequest:
            pass  # already answered earlier in the handler
    else:
        await event.answer(text, reply_markup=markup, disable_web_page_preview=True)


def sub_line(sub: Subscription | None, used: int | None = None) -> str:
    if sub is None:
        return "🔐 Активной VPN-подписки пока нет"
    status = STATUS.get(sub.status, sub.status)
    if sub.is_lifetime and sub.status == "active":
        when = "· бессрочно"
    elif sub.expires_at:
        when = f"· до {fmt_dt(sub.expires_at)}"
    else:
        when = ""
    devices = f"\nУстройства  <b>{used}</b> из {sub.device_limit}" if used is not None else ""
    return f"{status} {when}".strip() + devices


PLAN_NAMES = {"trial": "Пробный период", "bonus": "Бонусная", "admin": "Выдана администратором"}

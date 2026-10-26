from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.runtime import CAPTION_LIMIT, Logo

log = logging.getLogger("spirittvpn.ui")


async def render(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None, *, logo: Logo | None = None) -> None:
    """Shows a screen in place of the current message, switching between photo and text messages as needed."""
    msg = cb.message
    bot = cb.bot
    assert bot is not None
    chat_id = msg.chat.id if msg else cb.from_user.id
    use_logo = logo is not None and len(text) <= CAPTION_LIMIT
    try:
        if isinstance(msg, Message):
            if use_logo and msg.photo:
                await msg.edit_caption(caption=text, reply_markup=markup)
                return
            if not use_logo and msg.text is not None:
                await msg.edit_text(text, reply_markup=markup)
                return
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        log.debug("edit failed, sending new message: %s", exc)
    if use_logo:
        assert logo is not None
        await logo.send(bot, chat_id, text, markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)

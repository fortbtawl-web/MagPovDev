"""Builds the shared AppContext for the API, the bot and the worker."""
from __future__ import annotations

import logging

from backend.config import Settings, get_settings
from backend.database import make_engine, make_sessionmaker
from backend.integrations.payments import build_payment_provider
from backend.integrations.vpn import VpnRegistry
from backend.services.context import AppContext, NullNotifier

log = logging.getLogger("spiritt")


class TelegramNotifier:
    """Sends messages through the Bot API (aiogram Bot, HTML parse mode)."""

    def __init__(self, token: str):
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        self.bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def send(self, user_id: int, text: str, *, buttons: list[tuple[str, str]] | None = None) -> None:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = None
        if buttons:
            # (text, "https://…") -> URL button, anything else -> callback_data
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=t, url=u) if u.startswith("https://") else InlineKeyboardButton(text=t, callback_data=u)]
                    for t, u in buttons
                ]
            )
        try:
            await self.bot.send_message(user_id, text, reply_markup=markup, disable_web_page_preview=True)
        except Exception as exc:  # noqa: BLE001 - user blocked the bot etc.
            log.info("notify %s failed: %s", user_id, type(exc).__name__)

    async def close(self) -> None:
        await self.bot.session.close()


def setup_logging(settings: Settings) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Small rotating log: the target host has ~2 GB of disk.
        handlers.append(RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"))
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # aiogram logs request URLs on errors; the bot token is part of those URLs.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    # httpx logs full URLs at INFO; keep them quiet (tokens are in headers, but URLs may contain ids).
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_context(settings: Settings | None = None, *, notifier=None) -> AppContext:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url, settings.db_pool_size)
    token = settings.bot_token.get_secret_value()
    if notifier is None:
        notifier = TelegramNotifier(token) if token else NullNotifier()
    ctx = AppContext(
        settings=settings,
        sessionmaker=make_sessionmaker(engine),
        vpn=VpnRegistry.from_settings(settings),
        payments=build_payment_provider(settings),
        notifier=notifier,
    )
    ctx.extra["engine"] = engine
    bot = getattr(notifier, "bot", None)
    if bot is not None:
        from backend.services.access import BotChannelChecker

        ctx.extra["channel_checker"] = BotChannelChecker(bot, settings.required_channel)
        from backend.services.tasks import BotMemberChecker

        ctx.extra["member_checker"] = BotMemberChecker(bot)
    return ctx


async def close_context(ctx: AppContext) -> None:
    await ctx.vpn.close()
    if ctx.payments is not None:
        await ctx.payments.close()
    close = getattr(ctx.notifier, "close", None)
    if close:
        await close()
    engine = ctx.extra.get("engine")
    if engine is not None:
        await engine.dispose()

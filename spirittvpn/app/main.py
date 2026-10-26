"""Entry point: python -m app"""
from __future__ import annotations

import asyncio
import logging
import re
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent
from aiohttp import web
from sqlalchemy.exc import DBAPIError, OperationalError

from app import texts
from app.config import Settings, get_settings
from app.database import create_engine, create_session_factory
from app.handlers import admin, buy, user
from app.jobs import scheduler
from app.middlewares import DbSessionMiddleware, ThrottlingMiddleware, UserContextMiddleware
from app.payments.providers import build_payment_provider
from app.runtime import Deps, Logo
from app.services.orders import seed_plans
from app.vpn.factory import build_vpn_provider
from app.web import build_web_app

log = logging.getLogger("spirittvpn")


class RedactSecretsFilter(logging.Filter):
    """Strips bot tokens and Bearer tokens that might end up in library log lines."""

    PATTERNS = [re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"), re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+")]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = msg
        for p in self.PATTERNS:
            redacted = p.sub("[REDACTED]", redacted)
        if redacted != msg:
            record.msg, record.args = redacted, None
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecretsFilter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def build_storage(settings: Settings) -> BaseStorage:
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


def build_dispatcher(deps: Deps) -> Dispatcher:
    dp = Dispatcher(storage=build_storage(deps.settings), deps=deps)
    dp.update.outer_middleware(DbSessionMiddleware(deps))
    throttle = ThrottlingMiddleware(deps.settings.throttle_seconds)
    ctx = UserContextMiddleware(deps)
    for observer in (dp.message, dp.callback_query, dp.pre_checkout_query):
        observer.outer_middleware(ctx)
    dp.message.outer_middleware(throttle)
    dp.callback_query.outer_middleware(throttle)

    dp.include_router(admin.build_admin_router(deps))
    dp.include_router(admin.build_denied_router())
    dp.include_router(buy.router)
    dp.include_router(user.router)

    @dp.errors(ExceptionTypeFilter(OperationalError, DBAPIError))
    async def db_error(event: ErrorEvent) -> None:
        log.error("database unavailable: %s", type(event.exception).__name__)
        await _notify_error(event, texts.DB_UNAVAILABLE)

    @dp.errors()
    async def any_error(event: ErrorEvent) -> None:
        log.exception("unhandled error", exc_info=event.exception)
        await _notify_error(event, texts.GENERIC_ERROR)

    return dp


async def _notify_error(event: ErrorEvent, text: str) -> None:
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer(text[:190], show_alert=True)
        elif upd.message:
            await upd.message.answer(text)
    except Exception:  # noqa: BLE001
        pass


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    deps = Deps(settings, session_factory, build_vpn_provider(settings), build_payment_provider(settings), Logo())
    if not deps.logo.available():
        log.error("logo file missing or too large: %s — text fallback will be used", deps.logo.path)

    async with session_factory() as session:
        await seed_plans(session, settings.plan_prices, settings.payment_currency)

    bot = Bot(settings.bot_token.get_secret_value(),
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = build_dispatcher(deps)
    me = await bot.get_me()
    log.info("bot @%s started (env=%s, vpn=%s, payments=%s)", me.username, settings.app_env, deps.vpn.name, deps.payments.name)
    await bot.set_my_commands([
        BotCommand(command="start", description="Запуск и главное меню"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="support", description="Поддержка"),
        BotCommand(command="docs", description="Документы"),
        BotCommand(command="about", description="О проекте"),
    ])

    runner: web.AppRunner | None = None
    if settings.web_enabled:
        runner = web.AppRunner(build_web_app(deps, bot))
        await runner.setup()
        await web.TCPSite(runner, settings.web_host, settings.web_port).start()
        log.info("http server on %s:%s", settings.web_host, settings.web_port)

    jobs_task = asyncio.create_task(scheduler(deps, bot))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    polling = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False)
    )
    await asyncio.wait({polling, asyncio.create_task(stop.wait())}, return_when=asyncio.FIRST_COMPLETED)
    log.info("shutting down")
    try:
        await dp.stop_polling()
    except RuntimeError:
        pass
    jobs_task.cancel()
    polling.cancel()
    if runner:
        await runner.cleanup()
    await deps.vpn.close()
    await bot.session.close()
    await engine.dispose()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()

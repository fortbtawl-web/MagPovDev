"""Telegram bot entry point: python -m bot"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from backend.bootstrap import TelegramNotifier, build_context, close_context, setup_logging
from backend.config import get_settings
from backend.services import devices as dev_svc
from backend.services import roles
from bot.handlers import admin, cabinet, devices, gate, plans, promo, referrals, spircoin, start, support, trial_code, wizard
from bot.keyboards import is_https
from bot.middlewares import ErrorMiddleware, GateMiddleware, ThrottleMiddleware, UserMiddleware

log = logging.getLogger("spiritt.bot")


def build_dispatcher(ctx) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["ctx"] = ctx
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(ErrorMiddleware())
        observer.middleware(UserMiddleware())
        observer.middleware(GateMiddleware())
    dp.callback_query.outer_middleware(ThrottleMiddleware())
    dp.include_routers(gate.router, admin.router, admin.denied, start.router, promo.router, cabinet.router, support.router, plans.router, devices.router, referrals.router, spircoin.router, trial_code.router, wizard.router)
    return dp


async def _sync_menu_button(bot, settings) -> None:
    """Keeps the chat menu button on the current Mini App address (the tunnel may come up late or change)."""
    from backend import webapp_url

    shown = None
    warned = False
    while True:
        url = webapp_url.current(settings)
        if is_https(url) and url != shown:
            try:
                await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="SpirittVPN", web_app=WebAppInfo(url=url)))
                shown = url
                log.info("кнопка Mini App обновлена: %s", url)
            except Exception as exc:  # noqa: BLE001 - Telegram hiccup: try again on the next tick
                log.warning("не удалось обновить кнопку Mini App: %s", type(exc).__name__)
        elif not is_https(url) and not warned:
            log.warning("адрес Mini App ещё не получен — кнопка появится сама, как только поднимется туннель")
            warned = True
        await asyncio.sleep(20)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    from backend.config import config_warnings, missing_required

    problems = missing_required(settings, "bot")
    if problems:
        raise SystemExit("Не заданы обязательные переменные в .env:\n  - " + "\n  - ".join(problems))
    for w in config_warnings(settings):
        log.warning(w)
    ctx = build_context(settings)
    assert isinstance(ctx.notifier, TelegramNotifier)
    bot = ctx.notifier.bot
    me = await bot.get_me()
    ctx.extra["bot_username"] = me.username
    log.info("bot @%s started", me.username)
    await dev_svc.ensure_nodes(ctx)
    if not await roles.owner_exists(ctx):
        # Printed only to the server log: whoever can read it is the operator and may claim ownership.
        ctx.extra["setup_code"] = roles.new_setup_code()
        log.warning("Главный администратор не назначен. Отправьте боту: /setup %s  (или задайте OWNER_TELEGRAM_ID в .env)", ctx.extra["setup_code"])

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить SpirittVPN"),
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="cabinet", description="Профиль и подписка"),
        BotCommand(command="promo", description="Активировать промокод"),
        BotCommand(command="support", description="Связаться с поддержкой"),
        BotCommand(command="whoami", description="Мой SpirittVPN ID"),
    ])
    sync_task = asyncio.create_task(_sync_menu_button(bot, settings))

    dp = build_dispatcher(ctx)
    # Reached only after getMe and the DB check above succeeded: a failed start never announces "back online".
    from backend.services import broadcast as broadcast_svc

    try:
        if await broadcast_svc.on_startup_verified(ctx):
            log.info("уведомление о восстановлении работы поставлено в очередь рассылки")
    except Exception:  # noqa: BLE001 - never block the bot start because of the notice
        log.exception("maintenance notice failed")
    worker_task = None
    stop = asyncio.Event()
    if os.environ.get("SPIRITT_WORKER_IN_BOT") == "1" and settings.run_worker:
        from backend.tasks import worker

        log.info("фоновые задачи запущены в процессе бота (API не запущен)")
        worker_task = asyncio.create_task(worker.run_forever(ctx, stop))
    try:
        # start_polling handles SIGINT/SIGTERM and stops gracefully.
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=True)
    finally:
        stop.set()
        sync_task.cancel()
        if worker_task is not None:
            await worker_task
        await close_context(ctx)


if __name__ == "__main__":
    asyncio.run(main())

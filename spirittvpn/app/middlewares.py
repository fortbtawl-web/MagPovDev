"""Middlewares: DB session, user context (registration/ban/maintenance), anti-flood."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject
from aiogram.types import User as TgUser

from app import texts
from app.runtime import Deps
from app.services import settings_store
from app.services.users import get_user, register_user

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _is_payment_event(event: TelegramObject) -> bool:
    return isinstance(event, PreCheckoutQuery) or (isinstance(event, Message) and event.successful_payment is not None)


async def _reply(event: TelegramObject, text: str) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(text[:190], show_alert=True)
    elif isinstance(event, Message):
        await event.answer(text)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.deps.session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class UserContextMiddleware(BaseMiddleware):
    """Registers the user on first contact, refreshes profile; blocks banned users and maintenance mode.

    Payment updates are always let through so confirmed money is never lost.
    """

    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg: TgUser | None = data.get("event_from_user")
        if tg is None or tg.is_bot:
            return await handler(event, data)
        session = data["session"]
        now = datetime.now(UTC)
        user = await get_user(session, tg.id)
        created = False
        if user is None:
            user, created = await register_user(session, tg.id, tg.username, tg.first_name, now)
        elif user.username != tg.username or user.first_name != tg.first_name or user.bot_blocked or (
            now - user.last_seen_at > timedelta(minutes=10)
        ):
            user, _ = await register_user(session, tg.id, tg.username, tg.first_name, now)
        is_admin = self.deps.settings.is_admin(tg.id)
        data.update(user=user, user_created=created, is_admin=is_admin)

        if not is_admin and not _is_payment_event(event):
            if user.is_blocked:
                await _reply(event, texts.BLOCKED)
                return None
            if await settings_store.get_bool(session, "maintenance"):
                await _reply(event, await settings_store.get_setting(session, "maintenance_text"))
                return None
        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last: dict[int, float] = {}

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg: TgUser | None = data.get("event_from_user")
        if tg is None or _is_payment_event(event) or self.interval <= 0:
            return await handler(event, data)
        now = time.monotonic()
        if now - self._last.get(tg.id, 0.0) < self.interval:
            if isinstance(event, CallbackQuery):
                await event.answer(texts.THROTTLED)
            return None
        self._last[tg.id] = now
        if len(self._last) > 50_000:
            cutoff = now - 60
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return await handler(event, data)

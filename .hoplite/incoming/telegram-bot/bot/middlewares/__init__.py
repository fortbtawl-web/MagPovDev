from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from backend.services import referrals as ref_svc
from backend.services.context import AppContext, ServiceError

log = logging.getLogger("spiritt.bot")


class ThrottleMiddleware(BaseMiddleware):
    """Drops rapid repeated callback presses (double taps) per user."""

    def __init__(self, interval: float = 0.7):
        self.interval = interval
        self._last: dict[tuple[int, str], float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        if isinstance(event, CallbackQuery) and event.from_user:
            key = (event.from_user.id, event.data or "")
            now = time.monotonic()
            if now - self._last.get(key, 0) < self.interval:
                await event.answer("Уже обрабатываем…")
                return None
            self._last[key] = now
            if len(self._last) > 50_000:
                self._last.clear()
        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Makes sure the user exists. /start is skipped so the referral is attached by the start handler."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        ctx: AppContext = data["ctx"]
        is_start = isinstance(event, Message) and (event.text or "").startswith("/start")
        if user is not None and not user.is_bot and not is_start:
            async with ctx.session() as session, session.begin():
                reg = await ref_svc.register_user(
                    session,
                    ref_svc.TgProfile(user.id, user.username, user.first_name, user.last_name, user.language_code),
                )
            if reg.user.is_banned and not await _allowed_when_banned(event, data):
                await _reply(event, "🚫 Ваш аккаунт заблокирован. Связаться с поддержкой: /support")
                return None
        return await handler(event, data)


async def _allowed_when_banned(event: TelegramObject, data: dict[str, Any]) -> bool:
    """Blocked users keep access to support only."""
    if isinstance(event, CallbackQuery):
        d = event.data or ""
        return d.startswith("sup:") or d in ("nav:support", "nav:menu", "nav:cabinet")
    if isinstance(event, Message):
        text = event.text or ""
        if text.startswith(("/support", "/whoami", "/menu", "/cabinet", "/me")):
            return True
        state = data.get("state")
        current = await state.get_state() if state is not None else None
        return bool(current and current.startswith("SupportFSM"))
    return False


class ErrorMiddleware(BaseMiddleware):
    """Turns ServiceError into a friendly message; logs everything else without leaking details."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except ServiceError as exc:
            await _reply(event, f"△ {exc.message}")
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return None
            log.warning("telegram bad request: %s", exc)
            await _reply(event, "△ Сообщение устарело. Откройте меню заново: /start")
        except Exception:  # noqa: BLE001
            log.exception("unhandled bot error")
            await _reply(event, "△ Что-то пошло не так. Попробуйте ещё раз чуть позже.")
        return None


async def _reply(event: TelegramObject, text: str) -> None:
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(text[:200], show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
    except Exception:  # noqa: BLE001
        pass


class GateMiddleware(BaseMiddleware):
    """Blocks every action until documents are accepted and the channel subscription is confirmed.

    Runs on each update (not only /start): users who unsubscribe later lose access again.
    Only /start (shows the gate itself) and gate:* callbacks pass through.
    """

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        from backend.services import access

        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("gate:"):
            return await handler(event, data)
        ctx: AppContext = data["ctx"]
        st = await access.gate_state(ctx, user.id)
        if st.allowed:
            return await handler(event, data)
        state = data.get("state")
        if state is not None:
            await state.clear()
        from bot.handlers.gate import show_gate

        await show_gate(event, ctx, st)
        return None


class UserLocks:
    """Per-user lock for sensitive operations (purchase, trial, device changes) within this process."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]


user_locks = UserLocks()

"""Access gate: mandatory acceptance of documents + subscription to the news channel.

Checked on the server for every protected action (bot middleware and Mini App API dependency).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.models import TermsConsent, User
from backend.services import roles
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.access")
MEMBER_STATUSES = {"creator", "administrator", "member"}


class ChannelCheckError(Exception):
    """Telegram could not answer; access must NOT be granted."""


class ChannelChecker(Protocol):
    async def status(self, user_id: int) -> tuple[str, bool | None]:
        """Returns (status, is_member) as reported by getChatMember."""


class BotChannelChecker:
    def __init__(self, bot, channel: str, timeout: float = 10.0):
        self.bot, self.channel, self.timeout = bot, channel, timeout

    async def status(self, user_id: int) -> tuple[str, bool | None]:
        try:
            member = await asyncio.wait_for(self.bot.get_chat_member(chat_id=self.channel, user_id=user_id), self.timeout)
        except asyncio.TimeoutError as exc:
            raise ChannelCheckError("timeout") from exc
        except Exception as exc:  # noqa: BLE001 - TelegramBadRequest/Forbidden/Network etc.
            # Only the exception type and Telegram's description are logged, never the token.
            raise ChannelCheckError(f"{type(exc).__name__}: {getattr(exc, 'message', '')}".strip()) from exc
        status = str(getattr(member, "status", "") or "")
        status = getattr(status, "value", status)
        return str(status), getattr(member, "is_member", None)


def is_subscribed(status: str, is_member: bool | None) -> bool:
    if status in MEMBER_STATUSES:
        return True
    if status == "restricted":
        return bool(is_member)
    return False  # left, kicked, unknown


class AccessDenied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class GateState:
    terms_accepted: bool
    channel_required: bool
    channel_subscribed: bool | None
    channel_error: str | None = None

    @property
    def allowed(self) -> bool:
        return self.terms_accepted and (not self.channel_required or self.channel_subscribed is True)


CHANNEL_ERROR_TEXT = "Не удалось проверить подписку. Попробуйте ещё раз чуть позже — если не получится, напишите в поддержку."


def _cache(ctx: AppContext) -> dict[int, float]:
    return ctx.extra.setdefault("channel_cache", {})


def forget(ctx: AppContext, user_id: int) -> None:
    _cache(ctx).pop(user_id, None)


async def check_channel(ctx: AppContext, user_id: int, *, force: bool = False) -> tuple[bool | None, str | None]:
    """(subscribed, error). Only positive answers are cached, and only briefly."""
    s = ctx.settings
    cache = _cache(ctx)
    now = time.monotonic()
    if not force and cache.get(user_id, 0) > now:
        return True, None
    checker: ChannelChecker | None = ctx.extra.get("channel_checker")
    if checker is None:
        log.error("channel check impossible: no bot client configured")
        return None, CHANNEL_ERROR_TEXT
    try:
        status, is_member = await checker.status(user_id)
    except ChannelCheckError as exc:
        log.warning("getChatMember(%s) failed for user %s: %s — проверьте, что бот администратор канала", s.required_channel, user_id, exc)
        return None, CHANNEL_ERROR_TEXT
    ok = is_subscribed(status, is_member)
    if ok:
        cache[user_id] = now + max(0, s.channel_cache_seconds)
        if len(cache) > 100_000:
            cache.clear()
    else:
        cache.pop(user_id, None)
    return ok, None


async def gate_state(ctx: AppContext, user_id: int, *, force_channel: bool = False) -> GateState:
    s = ctx.settings
    async with ctx.session() as session:
        version = await session.scalar(select(User.terms_version).where(User.id == user_id))
    terms_ok = version == s.terms_version
    # Admins are exempt from the channel check so a misconfigured channel can't lock them out of the panel.
    channel_required = s.channel_check_enabled and bool(s.required_channel) and not await roles.is_admin(ctx, user_id)
    if not terms_ok or not channel_required:
        return GateState(terms_ok, channel_required, None if channel_required else True)
    subscribed, error = await check_channel(ctx, user_id, force=force_channel)
    return GateState(terms_ok, True, subscribed, error)


async def require_access(ctx: AppContext, user_id: int) -> GateState:
    st = await gate_state(ctx, user_id)
    if not st.terms_accepted:
        raise AccessDenied("terms_required", "Сначала примите политику конфиденциальности и пользовательское соглашение.")
    if not st.allowed:
        raise AccessDenied("channel_required", st.channel_error or "Подпишитесь на канал @spirit_vpn_news, чтобы пользоваться SpirittVPN.")
    return st


async def accept_terms(ctx: AppContext, user_id: int, version: str, source: str) -> None:
    s = ctx.settings
    if version != s.terms_version:
        raise ServiceError("Документы обновились. Откройте их ещё раз и примите актуальную версию.", "terms_outdated")
    async with ctx.session() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ServiceError("Нажмите /start.", "not_found")
        await session.execute(
            insert(TermsConsent)
            .values(user_id=user_id, version=version, privacy_url=s.privacy_url, terms_url=s.terms_url, source=source)
            .on_conflict_do_nothing(constraint="uq_terms_consents_user_version")
        )
        if user.terms_version != version:
            user.terms_version, user.terms_accepted_at = version, utcnow()


def gate_payload(ctx: AppContext, st: GateState) -> dict:
    s = ctx.settings
    return {
        "terms_accepted": st.terms_accepted,
        "terms_version": s.terms_version,
        "privacy_url": s.privacy_url,
        "terms_url": s.terms_url,
        "channel_required": st.channel_required,
        "channel_subscribed": st.channel_subscribed,
        "channel_url": s.required_channel_url if st.channel_required else None,
        "channel_error": st.channel_error,
        "allowed": st.allowed,
    }

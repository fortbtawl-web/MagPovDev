"""Shared runtime objects: dependency container, logo sender, notifier, payment delivery."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import keyboards as kb
from app import texts
from app.branding import LOGO_PATH
from app.config import Settings
from app.payments.providers import PaymentProvider
from app.services.formatting import fmt_dt, fmt_money
from app.services.orders import PaymentOutcome, PaymentResult
from app.services.ops import Notifier
from app.services.vpn_sync import sync_user_vpn
from app.vpn.base import VPNProvider

log = logging.getLogger("spirittvpn")

TELEGRAM_PHOTO_LIMIT = 10 * 1024 * 1024
CAPTION_LIMIT = 1024


class Logo:
    """Sends the original SpirittVPN logo from the local file; caches Telegram file_id after first upload."""

    def __init__(self, path: Path = LOGO_PATH) -> None:
        self.path = path
        self.file_id: str | None = None

    def available(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size <= TELEGRAM_PHOTO_LIMIT

    async def send(self, bot: Bot, chat_id: int, caption: str, reply_markup: InlineKeyboardMarkup | None) -> Message:
        """Photo + caption + keyboard; falls back to plain text (and logs) if the image can't be sent."""
        if len(caption) <= CAPTION_LIMIT:
            try:
                if self.file_id is None and not self.available():
                    raise FileNotFoundError(str(self.path))
                photo = self.file_id or FSInputFile(self.path, filename="spirittvpn_logo.png")
                msg = await bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup)
                if msg.photo:
                    self.file_id = msg.photo[-1].file_id
                return msg
            except (FileNotFoundError, TelegramBadRequest) as exc:
                log.error("logo send failed, falling back to text: %s", exc)
                self.file_id = None
        return await bot.send_message(chat_id, caption, reply_markup=reply_markup)


async def send_welcome(bot: Bot, chat_id: int, logo: Logo, welcome_text: str, first_run: bool, trial_days: int) -> Message:
    return await logo.send(bot, chat_id, texts.welcome_caption(welcome_text, first_run), kb.main_menu(trial_days))


@dataclass
class Deps:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    vpn: VPNProvider
    payments: PaymentProvider
    logo: Logo = field(default_factory=Logo)

    def fmt_dt(self, dt: datetime) -> str:
        return fmt_dt(dt, self.settings.display_timezone)


def make_notifier(bot: Bot) -> Notifier:
    async def notify(telegram_id: int, text: str, markup: InlineKeyboardMarkup | None) -> bool:
        for _ in range(3):
            try:
                await bot.send_message(telegram_id, text, reply_markup=markup)
                return True
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.5)
            except TelegramForbiddenError:
                return False
            except TelegramBadRequest as exc:
                if "chat not found" in str(exc).lower():
                    return False
                raise
        return False

    return notify


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as exc:
            log.warning("admin notify failed: %s", type(exc).__name__)


async def deliver_payment_result(bot: Bot, session: AsyncSession, deps: Deps, result: PaymentResult) -> None:
    """User/admin notifications after a payment event has been processed and committed."""
    order, user = result.order, result.user
    if result.outcome == PaymentOutcome.APPLIED and order and user and result.subscription:
        sub = result.subscription
        vpn_ok = await sync_user_vpn(session, deps.vpn, user, sub, datetime.now().astimezone(), deps.settings.vpn_retry_attempts)
        text = texts.payment_success(
            order.plan_title, deps.fmt_dt(sub.expires_at), fmt_money(order.amount, order.currency), order.id, order.is_test
        )
        if not vpn_ok:
            text += texts.VPN_PENDING_NOTE
        try:
            await deps.logo.send(bot, user.telegram_id, text, kb.after_payment())
        except TelegramAPIError as exc:
            log.warning("payment success notify failed: %s", type(exc).__name__)
    elif result.outcome == PaymentOutcome.FAILED and order and user:
        try:
            await bot.send_message(
                user.telegram_id,
                f"⚠️ <b>Платёж отклонён</b>\n\nЗаказ <code>{order.id}</code> не оплачен, деньги не списаны. "
                "Можно попробовать снова.",
                reply_markup=kb.expiring(),
            )
        except TelegramAPIError:
            pass
    elif result.outcome in (PaymentOutcome.MISMATCH, PaymentOutcome.UNKNOWN_ORDER):
        await notify_admins(
            bot, deps.settings,
            f"🚨 Платёж требует проверки: {result.outcome.value}, заказ <code>{order.id if order else '—'}</code>",
        )

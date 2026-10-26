"""End-to-end handler wiring: real aiogram Dispatcher with a mocked Bot API session."""
from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, PhotoSize, Update
from aiogram.types import User as TgUser
from sqlalchemy import select

from app import texts
from app.main import build_dispatcher
from app.models import LegalAcceptance, Subscription
from app.payments.providers import TestPaymentProvider
from app.runtime import Deps, Logo
from tests.conftest import ADMIN_ID


class MockSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        name = type(method).__name__
        if name in {"SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption", "SendDocument", "SendInvoice"}:
            photo = [PhotoSize(file_id="logo-file-id", file_unique_id="u", width=1254, height=1254)] if name == "SendPhoto" else None
            return Message(message_id=len(self.requests), date=datetime.now(UTC),
                           chat=Chat(id=getattr(method, "chat_id", None) or 1, type="private"), photo=photo,
                           text=getattr(method, "text", None))
        return True


@pytest.fixture
def bot_env(settings, session_factory, vpn):
    settings.throttle_seconds = 0
    deps = Deps(settings, session_factory, vpn, TestPaymentProvider("s3cret", None), Logo())
    session = MockSession()
    bot = Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    return build_dispatcher(deps), bot, session


_uid = iter(range(1, 10_000))


def tg_user(uid):
    return TgUser(id=uid, is_bot=False, first_name="U", username=f"u{uid}")


def cmd(uid, text):
    return Update(update_id=next(_uid), message=Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(id=uid, type="private"), from_user=tg_user(uid), text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))]))


def click(uid, data, photo=True):
    msg = Message(message_id=5, date=datetime.now(UTC), chat=Chat(id=uid, type="private"),
                  photo=[PhotoSize(file_id="p", file_unique_id="p", width=1, height=1)] if photo else None,
                  caption="x" if photo else None, text=None if photo else "x")
    return Update(update_id=next(_uid), callback_query=CallbackQuery(id=str(next(_uid)), from_user=tg_user(uid),
                                                                     chat_instance="ci", data=data, message=msg))


async def test_start_sends_logo_with_welcome_docs_and_menu(bot_env):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(5, "/start"))
    photo = next(r for r in s.requests if isinstance(r, SendPhoto))
    assert photo.caption.startswith("<b>SPIRITTVPN</b>") and "Добро пожаловать" not in photo.caption
    assert "POLITIKA-KONFIDENCIALNOSTI-08-12-99" in photo.caption and "PUBLICHNAYA-OFERTA-08-12-15" in photo.caption
    labels = [b.text for row in photo.reply_markup.inline_keyboard for b in row]
    assert labels == ["🚀 Открыть SpirittVPN", "👤 Мой аккаунт", "🛟 Поддержка", "📄 Документы"]
    # second /start reuses cached file_id (no re-upload) and keeps the user
    await dp.feed_update(bot, cmd(5, "/start"))
    photos = [r for r in s.requests if isinstance(r, SendPhoto)]
    assert photos[-1].photo == "logo-file-id"


async def test_non_admin_cannot_open_admin_panel(bot_env):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(5, "/admin"))
    await dp.feed_update(bot, cmd(5, "/extend 5 365 free"))
    sent = [r.text for r in s.requests if isinstance(r, SendMessage)]
    assert sent == [texts.ADMIN_DENIED, texts.ADMIN_DENIED]
    s.requests.clear()
    await dp.feed_update(bot, click(5, "adm:ext:5:30"))
    ans = next(r for r in s.requests if isinstance(r, AnswerCallbackQuery))
    assert ans.text == texts.ADMIN_DENIED


async def test_admin_can_open_panel(bot_env):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(ADMIN_ID, "/admin"))
    msg = next(r for r in s.requests if isinstance(r, SendMessage))
    assert "Панель администратора" in msg.text


async def test_trial_flow_requires_explicit_acceptance(bot_env, session_factory):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(7, "/start"))
    s.requests.clear()
    await dp.feed_update(bot, click(7, "trial"))
    gate = [r for r in s.requests if hasattr(r, "reply_markup") and r.reply_markup]
    labels = [b.text for r in gate for row in r.reply_markup.inline_keyboard for b in row]
    assert "✅ Принимаю условия" in labels
    async with session_factory() as db:
        assert await db.scalar(select(Subscription)) is None
        assert await db.scalar(select(LegalAcceptance)) is None
    s.requests.clear()
    await dp.feed_update(bot, click(7, "accept:trial"))
    async with session_factory() as db:
        acc = await db.scalar(select(LegalAcceptance))
        assert acc is not None and acc.version == "test-v1"
        sub = await db.scalar(select(Subscription))
        assert sub is not None and sub.is_trial
    shown = [r for r in s.requests if isinstance(r, (EditMessageCaption, SendPhoto))]
    assert any("Пробный период активирован" in (r.caption or "") for r in shown)


async def test_referral_start_binds_referrer(bot_env, session_factory):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(11, "/start"))
    await dp.feed_update(bot, cmd(12, "/start ref_11"))
    await dp.feed_update(bot, cmd(12, "/start ref_13"))
    from app.models import User

    async with session_factory() as db:
        u11 = await db.scalar(select(User).where(User.telegram_id == 11))
        u12 = await db.scalar(select(User).where(User.telegram_id == 12))
        assert u12.referrer_id == u11.id
    caption = [r for r in s.requests if isinstance(r, SendPhoto)][1].caption
    assert "пригласил друг" in caption


async def test_device_flow_in_fallback_menu(bot_env, session_factory):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(9, "/start"))
    await dp.feed_update(bot, click(9, "accept:trial"))
    s.requests.clear()
    await dp.feed_update(bot, click(9, "devices", photo=False))
    shown = " ".join(getattr(r, "text", "") or "" for r in s.requests)
    assert "Использовано 1 из 1" in shown


async def test_buy_shows_plans_only_with_prices(bot_env, session_factory):
    dp, bot, s = bot_env
    await dp.feed_update(bot, cmd(8, "/start"))
    s.requests.clear()
    await dp.feed_update(bot, click(8, "buy"))
    texts_shown = [getattr(r, "text", None) or getattr(r, "caption", None) for r in s.requests]
    assert any(t and "Тарифы скоро появятся" in t for t in texts_shown)

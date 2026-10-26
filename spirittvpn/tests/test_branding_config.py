"""Checks of mandatory links, contacts, roles, logo and configuration guards."""
import pytest
from pydantic import ValidationError

from app import branding, keyboards, texts
from app.config import Settings
from app.runtime import TELEGRAM_PHOTO_LIMIT, Logo

PRIVACY = "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
AGREEMENT = "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"


def buttons(markup):
    return [btn for row in markup.inline_keyboard for btn in row]


def by_text(markup):
    return {b.text: b for b in buttons(markup)}


def test_document_urls_exact_and_not_swapped():
    assert branding.PRIVACY_POLICY_URL == PRIVACY
    assert branding.USER_AGREEMENT_URL == AGREEMENT
    docs = by_text(keyboards.docs())
    assert docs["🔒 Политика конфиденциальности"].url == PRIVACY
    assert docs["📜 Пользовательское соглашение"].url == AGREEMENT


def test_main_menu_has_all_required_buttons():
    labels = set(by_text(keyboards.main_menu(3)))
    assert labels == {"🚀 Получить 3 дня бесплатно", "💳 Купить VPN", "🎟️ Ввести промокод", "👤 Мой аккаунт",
                      "🆘 Поддержка", "📄 Документы", "ℹ️ О проекте"}


def test_support_menu_contacts():
    sup = by_text(keyboards.support())
    assert sup["💬 Поддержка — @cupsov"].url == "https://t.me/cupsov"
    assert sup["💬 Поддержка — @TTwer4"].url == "https://t.me/TTwer4"
    assert {"❓ Частые вопросы", "🔌 Проблемы с подключением", "💳 Вопрос по оплате", "📄 Документы"} <= set(sup)
    assert "Напиши нашей команде поддержки — мы поможем разобраться" in texts.SUPPORT


def test_about_roles_are_correct():
    about = texts.about()
    assert "Овнер проекта:</b> <a href=\"https://t.me/TTwer4\">@TTwer4</a>" in about
    assert "Разработчик / кодер:</b> <a href=\"https://t.me/cupsov\">@cupsov</a>" in about
    assert "@cupsov</a> и <a href=\"https://t.me/TTwer4\">@TTwer4" in about
    kb = by_text(keyboards.about())
    assert kb["👑 Овнер — @TTwer4"].url == "https://t.me/TTwer4"
    assert kb["💻 Разработчик — @cupsov"].url == "https://t.me/cupsov"


def test_documents_available_in_all_required_places():
    for markup in (keyboards.account(), keyboards.support(), keyboards.about()):
        assert any(b.callback_data == "docs" for b in buttons(markup))
    for markup in (keyboards.plans([("m1", "x")]), keyboards.plan_checkout("m1", "pay"), keyboards.terms_gate("plan:m1", "buy")):
        urls = {b.url for b in buttons(markup)}
        assert {PRIVACY, AGREEMENT} <= urls
    first = texts.welcome_caption("hi", first_run=True)
    assert PRIVACY in first and AGREEMENT in first


def test_logo_is_original_png_within_telegram_limits():
    import hashlib

    data = branding.LOGO_PATH.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) <= TELEGRAM_PHOTO_LIMIT
    assert hashlib.sha256(data).hexdigest() == "eff64f3bdb12a3be6b8c8d8ee43d3e5a8fbcf6fe0dc71f5af36072d00e49d958"
    assert Logo().available()


async def test_logo_missing_falls_back_to_text(tmp_path):
    class FakeBot:
        def __init__(self):
            self.calls = []

        async def send_photo(self, *a, **k):
            self.calls.append("photo")

        async def send_message(self, chat_id, text, reply_markup=None):
            self.calls.append("text")
            return "msg"

    bot = FakeBot()
    assert await Logo(tmp_path / "missing.png").send(bot, 1, "hello", None) == "msg"
    assert bot.calls == ["text"]


def test_production_forbids_test_providers():
    base = dict(_env_file=None, bot_token="1:x", app_env="production")
    with pytest.raises(ValidationError):
        Settings(**base, payment_provider="test", payment_test_secret="x")
    with pytest.raises(ValidationError):
        Settings(**base, vpn_provider="test")
    s = Settings(**base, admin_ids="1, 2;3")
    assert s.admin_ids == frozenset({1, 2, 3})
    assert "1:x" not in repr(s)

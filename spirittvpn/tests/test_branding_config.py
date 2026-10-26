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
    assert docs["📜 Публичная оферта"].url == AGREEMENT


def test_new_start_screen_replaces_old_interface():
    from app.services.settings_store import DEFAULT_WELCOME

    with_app = keyboards.start("https://vpn.example/app/")
    labels = [b.text for row in with_app.inline_keyboard for b in row]
    assert labels == ["🚀 Открыть SpirittVPN", "👤 Мой аккаунт", "🛟 Поддержка", "📄 Документы"]
    assert with_app.inline_keyboard[0][0].web_app.url == "https://vpn.example/app/"
    fallback = keyboards.start(None)
    assert fallback.inline_keyboard[0][0].callback_data == "home" and fallback.inline_keyboard[0][0].web_app is None
    assert DEFAULT_WELCOME.startswith("<b>SPIRITTVPN</b>") and "Твоя свобода в сети." in DEFAULT_WELCOME
    old = ["Добро пожаловать", "Твой VPN начинается здесь", "Забирай 3 дня бесплатно", "Выбирай подходящий тариф",
           "Нажимай кнопку ниже", "Получить 3 дня бесплатно", "Купить VPN", "Ввести промокод"]
    import pathlib

    sources = "".join(p.read_text() for p in pathlib.Path("app").rglob("*.py"))
    for phrase in old:
        assert phrase not in sources, phrase


def test_webapp_url_requires_https():
    from app.config import Settings

    base = dict(_env_file=None, bot_token="1:x")
    assert Settings(**base, public_base_url="https://vpn.example").resolved_webapp_url == "https://vpn.example/app/"
    assert Settings(**base, public_base_url="http://vpn.example").resolved_webapp_url is None
    assert Settings(**base).resolved_webapp_url is None


def test_support_menu_contacts():
    sup = by_text(keyboards.support())
    assert sup["💬 Поддержка — @cupsov"].url == "https://t.me/cupsov"
    assert sup["💬 Поддержка — @TTwer4"].url == "https://t.me/TTwer4"
    assert {"❓ Частые вопросы", "🔌 Подключение", "💳 Оплата", "📄 Документы", "✍️ Создать обращение"} <= set(sup)
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
    for markup in (keyboards.account(True), keyboards.support(), keyboards.about(), keyboards.start(None)):
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
        Settings(**base, vpn_provider="mock")
    with pytest.raises(ValidationError):
        Settings(**base, vpn_provider="test")
    with pytest.raises(ValidationError):
        Settings(**base, vpn_provider="remnawave")  # explicit URL + token required
    s = Settings(**base, admin_ids="1, 2;3", owner_ids="9")
    assert s.admin_ids == frozenset({1, 2, 3}) and s.env_role(9) == "owner" and s.env_role(1) == "admin"
    assert "1:x" not in repr(s)

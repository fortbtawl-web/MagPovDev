"""Bot wiring: admin filter by Telegram ID, dispatcher assembly, keyboards."""
from __future__ import annotations

from types import SimpleNamespace

from bot.handlers.admin import IsAdmin
from bot.keyboards import main_menu
from bot.main import build_dispatcher
from tests.conftest import ADMIN_ID


async def test_admin_filter_uses_telegram_id(ctx):
    f = IsAdmin()
    assert await f(SimpleNamespace(from_user=SimpleNamespace(id=ADMIN_ID, username="anything")), ctx=ctx)
    # Username is irrelevant: a non-admin ID is refused even with an "admin" username.
    assert not await f(SimpleNamespace(from_user=SimpleNamespace(id=1, username="admin")), ctx=ctx)


def test_main_menu_buttons():
    kb = main_menu("https://app.example.com", is_admin=False)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    for t in ["◆ Открыть SpirittVPN", "🛡 Подписка", "📱 Устройства", "💎 Тарифы", "🎁 Пробный период", "🎟 Промокод", "👥 Друзья", "💳 Платежи", "👤 Профиль", "💬 Помощь и поддержка"]:
        assert t in texts
    assert kb.inline_keyboard[0][0].web_app.url == "https://app.example.com"
    # No Mini App button without HTTPS.
    assert "◆ Открыть SpirittVPN" not in [b.text for row in main_menu("http://x").inline_keyboard for b in row]


async def test_dispatcher_builds(ctx):
    dp = build_dispatcher(ctx)
    assert "message" in dp.resolve_used_update_types() and "callback_query" in dp.resolve_used_update_types()


async def test_bot_cabinet_view(ctx):
    from bot.handlers.cabinet import cabinet_view
    from backend.services import admin as admin_svc
    from tests.conftest import make_user

    reg = await make_user(ctx, 77)
    text, kb = await cabinet_view(ctx, 77)
    assert reg.user.public_id in text and "нет активной VPN-подписки" in text
    await admin_svc.add_days(ctx, ADMIN_ID, "web", 77, 10, "тест кабинета")
    text, kb = await cabinet_view(ctx, 77)
    assert "Действует до" in text and "9 дн." in text or "10 дн." in text
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert "⚡ Ссылка подписки и QR" in buttons and "⚙ Админ-панель" not in buttons

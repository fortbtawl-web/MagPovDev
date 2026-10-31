"""SpirittVPN message design system (Telegram HTML).

One visual language for every bot screen:
  title line   ◆ <b>Раздел</b>   (+ optional italic subtitle)
  info cards   <blockquote> with bold labels
  statuses     ● Активна / ◐ Оформляется / ○ Истекла  (geometric dots, no random emoji)
  navigation   ‹ Назад · ⌂ Меню
"""
from __future__ import annotations

from html import escape

from aiogram.types import InlineKeyboardButton

BRAND_NAME = "SpirittVPN"
TAGLINE = "Твоя свобода в сети"
BRAND = f"◆ <b>{BRAND_NAME}</b>  <i>{TAGLINE}</i>"

BACK = "‹ Назад"
HOME = "⌂ Главное меню"

STATUS = {
    "active": "🟢 Активна",
    "pending": "🟡 Оформляется",
    "expired": "⚪ Истекла",
    "suspended": "🟠 Приостановлена",
    "revoking": "🟠 Отзывается",
}


DIVIDER = "━━━━━━━━━━━━━━━━"

# Section icon by title prefix: one emoji per section, the same everywhere.
ICONS = (
    ("Моя подписка", "🛡"), ("Подписка", "🛡"), ("Тарифы", "💎"), ("Тариф", "💎"), ("Заказ", "🧾"), ("Покупка", "🧾"),
    ("Платежи", "💳"), ("История", "🕘"), ("Устройств", "📱"), ("Новое устройство", "📱"), ("Пробный", "🎁"),
    ("Код", "🔑"), ("Промокод", "🎟"), ("Друзья", "👥"), ("Приглаш", "👥"), ("Бонус", "🎁"), ("Задания", "🎯"),
    ("SpirCoin", "🪙"), ("Профиль", "👤"), ("Ваш аккаунт", "👤"), ("Поддержка", "💬"), ("Обращени", "💬"),
    ("Как подключиться", "🧭"), ("Ссылка подписки", "🔗"), ("Шаг", "🧭"), ("Нужна инструкция", "🧭"),
    ("Администр", "⚙️"), ("Админ", "⚙️"), ("Остался один шаг", "📢"), ("Документы", "📄"),
)


def _icon(text: str) -> str:
    for prefix, icon in ICONS:
        if text.startswith(prefix):
            return icon
    return "◆"


def title(text: str, subtitle: str | None = None) -> str:
    head = f"{_icon(text)} <b>{text}</b>"
    if subtitle:
        head += f"\n<i>{subtitle}</i>"
    return head + "\n" + DIVIDER


def no_subscription(hint: str = "Получите её в разделе «💎 Тарифы» или активируйте пробный период.") -> str:
    return card("🔐 <b>У вас пока нет активной VPN-подписки</b>", hint)


def card(*lines: str) -> str:
    """Highlighted block. Empty strings are dropped."""
    body = "\n".join(line for line in lines if line)
    return f"<blockquote>{body}</blockquote>"


def kv(label: str, value: object) -> str:
    return f"<b>{label}</b>  {value}"


def steps(items: list[str]) -> str:
    return "\n".join(f"<b>{i}</b>  {text}" for i, text in enumerate(items, 1))


def note(text: str) -> str:
    return f"<i>{text}</i>"


def ok(text: str) -> str:
    return f"✓ <b>{text}</b>"


def warn(text: str) -> str:
    return f"△ {text}"


def screen(head: str, *blocks: str) -> str:
    """Join blocks with a blank line between them."""
    return "\n\n".join(b for b in (head, *blocks) if b)


def safe(value: object) -> str:
    return escape(str(value))


def nav_row(back_to: str | None = None, back_text: str = BACK, *, home: bool = True) -> list[InlineKeyboardButton]:
    """‹ Назад (to a section) and/or ⌂ Главное меню."""
    from bot.keyboards import Nav

    row: list[InlineKeyboardButton] = []
    if back_to and back_to != "menu":
        row.append(InlineKeyboardButton(text=back_text, callback_data=Nav(to=back_to).pack()))
    if home:
        row.append(InlineKeyboardButton(text=HOME if not row else "⌂ Меню", callback_data=Nav(to="menu").pack()))
    return row

"""Inline keyboards. Callback data is short and validated by handlers."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup

from app import branding as b
from app.texts import trial_days_label

HOME = Btn(text="🏠 Главное меню", callback_data="menu")


def _kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def doc_buttons() -> list[Btn]:
    return [
        Btn(text="🔒 Политика конфиденциальности", url=b.PRIVACY_POLICY_URL),
        Btn(text="📜 Пользовательское соглашение", url=b.USER_AGREEMENT_URL),
    ]


def main_menu(trial_days: int = 3) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text=trial_days_label(trial_days), callback_data="trial")],
        [Btn(text="💳 Купить VPN", callback_data="buy"), Btn(text="🎟️ Ввести промокод", callback_data="promo")],
        [Btn(text="👤 Мой аккаунт", callback_data="account"), Btn(text="🆘 Поддержка", callback_data="support")],
        [Btn(text="📄 Документы", callback_data="docs"), Btn(text="ℹ️ О проекте", callback_data="about")],
    )


def docs(accept_next: str | None = None) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb(
        [d[0]],
        [d[1]],
        [Btn(text="✅ Принимаю условия", callback_data=f"accept:{accept_next}")] if accept_next else [],
        [HOME],
    )


def terms_gate(next_action: str, back: str) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb([d[0]], [d[1]], [Btn(text="✅ Принимаю условия", callback_data=f"accept:{next_action}")],
               [Btn(text="← Назад", callback_data=back), HOME])


def about() -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text=f"👑 Овнер — @{b.OWNER_USERNAME}", url=b.tg_url(b.OWNER_USERNAME))],
        [Btn(text=f"💻 Разработчик — @{b.DEVELOPER_USERNAME}", url=b.tg_url(b.DEVELOPER_USERNAME))],
        [Btn(text="🆘 Поддержка", callback_data="support"), Btn(text="📄 Документы", callback_data="docs")],
        [HOME],
    )


def support(extra_url: str = "") -> InlineKeyboardMarkup:
    contacts = [[Btn(text=f"💬 Поддержка — @{u}", url=b.tg_url(u))] for u in b.SUPPORT_USERNAMES]
    return _kb(
        *contacts,
        [Btn(text="💬 Написать в поддержку через бота", callback_data="ticket")],
        [Btn(text="🌐 Канал / чат поддержки", url=extra_url)] if extra_url else [],
        [Btn(text="❓ Частые вопросы", callback_data="faq:general")],
        [Btn(text="🔌 Проблемы с подключением", callback_data="faq:conn"), Btn(text="💳 Вопрос по оплате", callback_data="faq:pay")],
        [Btn(text="📄 Документы", callback_data="docs"), Btn(text="ℹ️ О проекте", callback_data="about")],
        [HOME],
    )


def back_to(cb: str, label: str = "← Назад") -> InlineKeyboardMarkup:
    return _kb([Btn(text=label, callback_data=cb), HOME])


def after_trial(has_connection: bool) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="📲 Подключить VPN", callback_data="conn")] if has_connection else [],
        [Btn(text="💳 Выбрать тариф", callback_data="buy")],
        [HOME],
    )


def trial_failed() -> InlineKeyboardMarkup:
    return _kb([Btn(text="💳 Выбрать тариф", callback_data="buy")], [Btn(text="🆘 Поддержка", callback_data="support")], [HOME])


def account() -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="🔑 Мои подключения", callback_data="conn"), Btn(text="💳 Продлить подписку", callback_data="buy")],
        [Btn(text="📜 История платежей", callback_data="history"), Btn(text="🎟️ Мои бонусы", callback_data="bonuses")],
        [Btn(text="🆘 Поддержка", callback_data="support"), Btn(text="📄 Документы", callback_data="docs")],
        [Btn(text="ℹ️ О проекте", callback_data="about")],
        [HOME],
    )


def plans(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return _kb(*[[Btn(text=label, callback_data=f"plan:{code}")] for code, label in items],
               [Btn(text="🎟️ Ввести промокод", callback_data="promo")], doc_buttons()[:1], doc_buttons()[1:], [HOME])


def plan_checkout(code: str, pay_label: str) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb([Btn(text=pay_label, callback_data=f"pay:{code}")], [d[0]], [d[1]],
               [Btn(text="← К тарифам", callback_data="buy"), HOME])


def payment_link(url: str, order_id: str, test: bool) -> InlineKeyboardMarkup:
    label = "🧪 Перейти к ТЕСТОВОЙ оплате" if test else "💳 Перейти к оплате"
    return _kb([Btn(text=label, url=url)], [Btn(text="🔄 Проверить статус заказа", callback_data=f"order:{order_id[:36]}")], [HOME])


def order_status(order_id: str) -> InlineKeyboardMarkup:
    return _kb([Btn(text="🔄 Обновить статус", callback_data=f"order:{order_id}")],
               [Btn(text="💳 Вопрос по оплате", callback_data="faq:pay")], [HOME])


def after_payment() -> InlineKeyboardMarkup:
    return _kb([Btn(text="📲 Подключить VPN", callback_data="conn")], [Btn(text="👤 Мой аккаунт", callback_data="account")], [HOME])


def cancel() -> InlineKeyboardMarkup:
    return _kb([Btn(text="✖️ Отмена", callback_data="cancel")])


def expiring() -> InlineKeyboardMarkup:
    return _kb([Btn(text="💳 Продлить подписку", callback_data="buy")], [HOME])


# ---------- admin ----------

def admin_menu() -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="📊 Статистика", callback_data="adm:stats"), Btn(text="👥 Пользователи", callback_data="adm:users")],
        [Btn(text="💳 Тарифы", callback_data="adm:plans"), Btn(text="🎟️ Промокоды", callback_data="adm:promos")],
        [Btn(text="⚙️ Настройки", callback_data="adm:settings"), Btn(text="📢 Рассылка", callback_data="adm:broadcast")],
        [Btn(text="🎫 Обращения", callback_data="adm:tickets"), Btn(text="🧾 Журнал аудита", callback_data="adm:audit")],
    )


def admin_back() -> InlineKeyboardMarkup:
    return _kb([Btn(text="← Админ-меню", callback_data="adm:menu")])


def admin_user(tg_id: int, blocked: bool) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="+7 дней", callback_data=f"adm:ext:{tg_id}:7"), Btn(text="+30 дней", callback_data=f"adm:ext:{tg_id}:30")],
        [Btn(text="✅ Разблокировать" if blocked else "⛔️ Заблокировать", callback_data=f"adm:blk:{tg_id}:{0 if blocked else 1}")],
        [Btn(text="← Админ-меню", callback_data="adm:menu")],
    )


def broadcast_confirm(bid: int) -> InlineKeyboardMarkup:
    return _kb([Btn(text="✅ Отправить всем", callback_data=f"adm:bc:send:{bid}"), Btn(text="✖️ Отмена", callback_data=f"adm:bc:cancel:{bid}")])

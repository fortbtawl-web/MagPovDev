"""Inline keyboards for the bot. The bot is an entry point to the Mini App; menus stay short."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as Btn
from aiogram.types import InlineKeyboardMarkup, WebAppInfo

from app import branding as b

HOME = Btn(text="🏠 Главное меню", callback_data="home")


def _kb(*rows: list[Btn]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def doc_buttons() -> list[Btn]:
    return [
        Btn(text="🔒 Политика конфиденциальности", url=b.PRIVACY_POLICY_URL),
        Btn(text="📜 Публичная оферта", url=b.USER_AGREEMENT_URL),
    ]


def open_app_button(webapp_url: str | None, path: str = "") -> Btn:
    if webapp_url:
        return Btn(text="🚀 Открыть SpirittVPN", web_app=WebAppInfo(url=webapp_url + path))
    return Btn(text="🚀 Открыть SpirittVPN", callback_data="home")


def start(webapp_url: str | None) -> InlineKeyboardMarkup:
    return _kb(
        [open_app_button(webapp_url)],
        [Btn(text="👤 Мой аккаунт", callback_data="account"), Btn(text="🛟 Поддержка", callback_data="support")],
        [Btn(text="📄 Документы", callback_data="docs")],
    )


def home(webapp_url: str | None) -> InlineKeyboardMarkup:
    """Fallback menu on plain messages when the Mini App is unavailable."""
    return _kb(
        [open_app_button(webapp_url)] if webapp_url else [],
        [Btn(text="💳 Подписка", callback_data="sub"), Btn(text="📱 Устройства", callback_data="devices")],
        [Btn(text="👥 Рефералы", callback_data="refs"), Btn(text="👤 Аккаунт", callback_data="account")],
        [Btn(text="🛟 Поддержка", callback_data="support"), Btn(text="📄 Документы", callback_data="docs")],
    )


def docs(accept_next: str | None = None) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb([d[0]], [d[1]],
               [Btn(text="✅ Принимаю условия", callback_data=f"accept:{accept_next}")] if accept_next else [],
               [Btn(text="ℹ️ О проекте", callback_data="about")], [HOME])


def terms_gate(next_action: str, back: str) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb([d[0]], [d[1]], [Btn(text="✅ Принимаю условия", callback_data=f"accept:{next_action}")],
               [Btn(text="← Назад", callback_data=back), HOME])


def about() -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text=f"👑 Овнер — @{b.OWNER_USERNAME}", url=b.tg_url(b.OWNER_USERNAME))],
        [Btn(text=f"💻 Разработчик — @{b.DEVELOPER_USERNAME}", url=b.tg_url(b.DEVELOPER_USERNAME))],
        [Btn(text="🛟 Поддержка", callback_data="support"), Btn(text="📄 Документы", callback_data="docs")],
        [HOME],
    )


def support(extra_url: str = "") -> InlineKeyboardMarkup:
    contacts = [[Btn(text=f"💬 Поддержка — @{u}", url=b.tg_url(u))] for u in b.SUPPORT_USERNAMES]
    return _kb(
        *contacts,
        [Btn(text="✍️ Создать обращение", callback_data="ticket")],
        [Btn(text="🌐 Канал / чат поддержки", url=extra_url)] if extra_url else [],
        [Btn(text="❓ Частые вопросы", callback_data="faq:general")],
        [Btn(text="🔌 Подключение", callback_data="faq:conn"), Btn(text="💳 Оплата", callback_data="faq:pay")],
        [Btn(text="📄 Документы", callback_data="docs"), Btn(text="ℹ️ О проекте", callback_data="about")],
        [HOME],
    )


def ticket_categories() -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="🔌 Подключение", callback_data="tcat:connection"), Btn(text="💳 Оплата", callback_data="tcat:payment")],
        [Btn(text="📅 Подписка", callback_data="tcat:subscription"), Btn(text="💬 Другое", callback_data="tcat:other")],
        [Btn(text="✖️ Отмена", callback_data="support")],
    )


def back_to(cb: str, label: str = "← Назад") -> InlineKeyboardMarkup:
    return _kb([Btn(text=label, callback_data=cb), HOME])


def account(notifications: bool) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="💳 Подписка", callback_data="sub"), Btn(text="📱 Устройства", callback_data="devices")],
        [Btn(text="📜 Платежи", callback_data="history"), Btn(text="🎁 Бонусы", callback_data="bonuses")],
        [Btn(text=f"🔔 Уведомления: {'вкл' if notifications else 'выкл'}", callback_data="notif")],
        [Btn(text="📄 Документы", callback_data="docs"), Btn(text="🛟 Поддержка", callback_data="support")],
        [HOME],
    )


def subscription(trial_available: bool, active: bool) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="🎁 Пробный период", callback_data="trial")] if trial_available else [],
        [Btn(text="💳 Продлить подписку" if active else "💳 Выбрать тариф", callback_data="buy")],
        [Btn(text="🎟 Промокод", callback_data="promo")],
        [HOME],
    )


def after_trial() -> InlineKeyboardMarkup:
    return _kb([Btn(text="📱 Мои устройства", callback_data="devices")], [HOME])


def trial_failed() -> InlineKeyboardMarkup:
    return _kb([Btn(text="💳 Выбрать тариф", callback_data="buy")], [Btn(text="🛟 Поддержка", callback_data="support")], [HOME])


def plans(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb(*[[Btn(text=label, callback_data=f"plan:{code}")] for code, label in items],
               [Btn(text="🎟 Промокод", callback_data="promo")], [d[0]], [d[1]], [HOME])


def plan_checkout(code: str, pay_label: str) -> InlineKeyboardMarkup:
    d = doc_buttons()
    return _kb([Btn(text=pay_label, callback_data=f"pay:{code}")], [d[0]], [d[1]],
               [Btn(text="← К тарифам", callback_data="buy"), HOME])


def payment_link(url: str, order_id: str, test: bool) -> InlineKeyboardMarkup:
    label = "🧪 Перейти к ТЕСТОВОЙ оплате" if test else "💳 Перейти к оплате"
    return _kb([Btn(text=label, url=url)], [Btn(text="🔄 Статус заказа", callback_data=f"order:{order_id[:36]}")], [HOME])


def order_status(order_id: str) -> InlineKeyboardMarkup:
    return _kb([Btn(text="🔄 Статус заказа", callback_data=f"order:{order_id}")],
               [Btn(text="💳 Вопрос по оплате", callback_data="faq:pay")], [HOME])


def after_payment() -> InlineKeyboardMarkup:
    return _kb([Btn(text="📱 Мои устройства", callback_data="devices")], [HOME])


def devices(items: list[tuple[str, str]], can_add: bool) -> InlineKeyboardMarkup:
    return _kb(*[[Btn(text=label, callback_data=f"dv:o:{pid}")] for pid, label in items],
               [Btn(text="➕ Добавить устройство", callback_data="dv:add")] if can_add else [],
               [Btn(text="❓ Как подключиться", callback_data="faq:conn")], [HOME])


def device(pid: str, active: bool) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="🔗 Ссылка и QR-код", callback_data=f"dv:c:{pid}")] if active else [],
        [Btn(text="✏️ Переименовать", callback_data=f"dv:n:{pid}"), Btn(text="🔁 Заменить", callback_data=f"dv:r:{pid}")],
        [Btn(text="🗑 Отозвать доступ", callback_data=f"dv:x:{pid}")],
        [Btn(text="← К устройствам", callback_data="devices")],
    )


def confirm(yes_cb: str, no_cb: str, yes_label: str) -> InlineKeyboardMarkup:
    return _kb([Btn(text=yes_label, callback_data=yes_cb)], [Btn(text="✖️ Отмена", callback_data=no_cb)])


def referrals(share: str | None) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="📤 Поделиться ссылкой", url=share)] if share else [],
        [Btn(text="📊 Мои приглашения", callback_data="refs:inv"), Btn(text="🎁 История бонусов", callback_data="bonuses")],
        [HOME],
    )


def cancel() -> InlineKeyboardMarkup:
    return _kb([Btn(text="✖️ Отмена", callback_data="cancel")])


def device_name_quick(default: str) -> InlineKeyboardMarkup:
    return _kb([Btn(text=f"Назвать «{default}»", callback_data="dv:quick")], [Btn(text="✖️ Отмена", callback_data="cancel")])


def expiring() -> InlineKeyboardMarkup:
    return _kb([Btn(text="💳 Продлить подписку", callback_data="buy")], [HOME])


# ---------- admin (bot) ----------

def admin_menu(webapp_url: str | None) -> InlineKeyboardMarkup:
    return _kb(
        [Btn(text="🛡 Открыть админ-панель", web_app=WebAppInfo(url=webapp_url + "#admin"))] if webapp_url else [],
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

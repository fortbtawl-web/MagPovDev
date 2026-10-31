from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder


class Nav(CallbackData, prefix="nav"):
    to: str  # menu | sub | devices | plans | trial | ref | ref_invites | ref_bonus | history | support | bonus_apply


class PlanCb(CallbackData, prefix="plan"):
    action: str  # view | buy
    code: str


class OrderCb(CallbackData, prefix="ord"):
    action: str  # check
    id: int


class DevCb(CallbackData, prefix="dev"):
    action: str  # view | link | add | addnode | rename | revoke | revoke_ok | replace | replace_node
    id: int = 0
    node: str = ""


class TrialCb(CallbackData, prefix="trial"):
    action: str  # go


def is_https(url: str | None) -> bool:
    return bool(url) and url.startswith("https://")


def main_menu(webapp_url: str | None, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_https(webapp_url):
        b.row(InlineKeyboardButton(text="◆ Открыть SpirittVPN", web_app=WebAppInfo(url=webapp_url)))
    b.row(
        InlineKeyboardButton(text="⚡ Подключиться", callback_data=Nav(to="mysub").pack()),
        InlineKeyboardButton(text="🧭 Инструкция", callback_data=Nav(to="howto").pack()),
    )
    b.row(
        InlineKeyboardButton(text="💎 Тарифы", callback_data=Nav(to="plans").pack()),
        InlineKeyboardButton(text="🛡 Подписка", callback_data=Nav(to="sub").pack()),
    )
    b.row(
        InlineKeyboardButton(text="📱 Устройства", callback_data=Nav(to="devices").pack()),
        InlineKeyboardButton(text="🎁 Пробный период", callback_data=Nav(to="trial").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎯 Задания", callback_data=Nav(to="tasks").pack()),
        InlineKeyboardButton(text="🪙 SpirCoin", callback_data=Nav(to="coins").pack()),
    )
    b.row(
        InlineKeyboardButton(text="🎟 Промокод", callback_data=Nav(to="promo").pack()),
        InlineKeyboardButton(text="👥 Друзья", callback_data=Nav(to="ref").pack()),
    )
    b.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data=Nav(to="cabinet").pack()),
        InlineKeyboardButton(text="💳 Платежи", callback_data=Nav(to="history").pack()),
    )
    b.row(InlineKeyboardButton(text="💬 Помощь и поддержка", callback_data=Nav(to="support").pack()))
    if is_admin:
        b.row(InlineKeyboardButton(text="⚙ Админ-панель", callback_data="adm:home"))
    return b.as_markup()


def back_row(to: str = "menu", text: str = "⌂ Главное меню") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=Nav(to=to).pack())]


def only_back(to: str = "menu", text: str = "⌂ Главное меню") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[back_row(to, text)])


def copy_button(text: str, value: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, copy_text=CopyTextButton(text=value[:256]))

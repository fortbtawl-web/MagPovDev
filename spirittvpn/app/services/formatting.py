from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZERO_DECIMAL_CURRENCIES = {"XTR", "JPY", "KRW"}
CURRENCY_SIGNS = {"RUB": "₽", "USD": "$", "EUR": "€", "XTR": "⭐"}


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def fmt_money(amount: int, currency: str) -> str:
    sign = CURRENCY_SIGNS.get(currency, currency)
    if currency in ZERO_DECIMAL_CURRENCIES:
        return f"{amount} {sign}"
    major, minor = divmod(amount, 100)
    return f"{major:,}".replace(",", " ") + (f".{minor:02d}" if minor else "") + f" {sign}"


def min_amount(currency: str) -> int:
    return 1 if currency in ZERO_DECIMAL_CURRENCIES else 100


def fmt_dt(dt: datetime, tz: str) -> str:
    local = dt.astimezone(ZoneInfo(tz))
    offset = local.strftime("%z")
    label = "МСК" if tz == "Europe/Moscow" else f"UTC{offset[:3]}:{offset[3:]}"
    return f"{local:%d.%m.%Y %H:%M} ({label})"


def fmt_remaining(delta: timedelta) -> str:
    total = max(int(delta.total_seconds()), 0)
    days, rest = divmod(total, 86400)
    hours = rest // 3600
    if days == 0 and hours == 0:
        return "менее часа"
    parts = []
    if days:
        parts.append(days_word(days))
    if hours:
        parts.append(f"{hours} {plural(hours, 'час', 'часа', 'часов')}")
    return " ".join(parts)

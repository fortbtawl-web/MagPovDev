"""Runtime-editable settings stored in app_settings (managed from the admin bot / web panel)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AppSetting
from backend.services.context import ServiceError

# key -> (default, type, label)
SETTINGS_SCHEMA: dict[str, tuple[Any, type, str]] = {
    "trial_enabled": (True, bool, "Пробный период включён"),
    "trial_days": (3, int, "Длительность пробного периода, дней"),
    "purchases_enabled": (True, bool, "Продажи включены"),
    "referral_enabled": (True, bool, "Реферальная программа включена"),
    "referral_trial_bonus_enabled": (True, bool, "Бонус за пробный период приглашённого"),
    "referral_trial_bonus_days": (3, int, "Бонус за пробный период, дней"),
    "referral_payment_bonus_enabled": (True, bool, "Бонус за первую оплату приглашённого"),
    "referral_payment_bonus_days": (7, int, "Бонус за первую оплату, дней"),
    "auto_provision_first_device": (True, bool, "Создавать первое устройство автоматически после оплаты"),
    "node_client_device_limit": (1, int, "Лимит устройств на один VPN-ключ на ноде (0 — без лимита)"),
    "lifetime_node_horizon_days": (365, int, "Технический срок ключа Lifetime на ноде (продлевается автоматически)"),
    "max_device_replacements_per_day": (5, int, "Максимум замен устройств в сутки"),
    "support_enabled": (True, bool, "Поддержка: приём обращений включён"),
    "support_max_open_tickets": (3, int, "Поддержка: максимум открытых обращений на пользователя"),
    "support_daily_limit": (2, int, "Поддержка: сколько обращений пользователь может создать в сутки"),
    "coins_enabled": (True, bool, "SpirCoin: покупка подписок за монеты включена"),
    "coins_daily_emission_limit": (0, int, "SpirCoin: максимум новых монет в сутки (задания + начисления), 0 — без лимита"),
    "coins_monthly_emission_limit": (0, int, "SpirCoin: максимум новых монет в месяц, 0 — без лимита"),
    "coins_admin_max_per_operation": (0, int, "SpirCoin: максимум одной операции администратора, 0 — без лимита"),
    "trial_code_after_trial": (False, bool, "Коды тестовых подписок: разрешить тем, кто уже получал обычный пробный период"),
    "trial_codes_admin_access": (False, bool, "Коды тестовых подписок: доступ администраторов (не только владельца)"),
}

# Economy limits are changed by the owner only (see admin.update_setting).
OWNER_ONLY = frozenset({
    "coins_enabled", "coins_daily_emission_limit", "coins_monthly_emission_limit", "coins_admin_max_per_operation",
    "trial_code_after_trial", "trial_codes_admin_access",
})

INT_BOUNDS: dict[str, tuple[int, int]] = {
    "trial_days": (1, 30),
    "referral_trial_bonus_days": (1, 365),
    "referral_payment_bonus_days": (1, 365),
    "node_client_device_limit": (0, 20),
    "lifetime_node_horizon_days": (30, 3650),
    "max_device_replacements_per_day": (0, 100),
    "support_max_open_tickets": (1, 20),
    "support_daily_limit": (1, 50),
    "coins_daily_emission_limit": (0, 100_000_000),
    "coins_monthly_emission_limit": (0, 1_000_000_000),
    "coins_admin_max_per_operation": (0, 10_000_000),
}


def coerce(key: str, value: Any) -> Any:
    if key not in SETTINGS_SCHEMA:
        raise ServiceError(f"Неизвестная настройка {key}", "bad_setting")
    _, typ, _ = SETTINGS_SCHEMA[key]
    if typ is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("1", "true", "yes", "on", "да"):
            return True
        if isinstance(value, str) and value.lower() in ("0", "false", "no", "off", "нет"):
            return False
        raise ServiceError(f"{key}: ожидается да/нет", "bad_setting")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(f"{key}: ожидается целое число", "bad_setting") from exc
    lo, hi = INT_BOUNDS.get(key, (0, 10**6))
    if not lo <= number <= hi:
        raise ServiceError(f"{key}: допустимо от {lo} до {hi}", "bad_setting")
    return number


async def get_all(session: AsyncSession) -> dict[str, Any]:
    values = {k: d for k, (d, _, _) in SETTINGS_SCHEMA.items()}
    rows = (await session.execute(select(AppSetting))).scalars().all()
    for row in rows:
        if row.key in values:
            values[row.key] = row.value
    return values


async def get(session: AsyncSession, key: str) -> Any:
    row = await session.get(AppSetting, key)
    return row.value if row else SETTINGS_SCHEMA[key][0]


async def put(session: AsyncSession, key: str, value: Any) -> tuple[Any, Any]:
    """Returns (old, new). Caller is responsible for the audit record and commit."""
    new = coerce(key, value)
    old = await get(session, key)
    stmt = insert(AppSetting).values(key=key, value=new)
    stmt = stmt.on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": new})
    await session.execute(stmt)
    return old, new

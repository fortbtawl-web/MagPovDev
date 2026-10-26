"""Runtime settings editable by admins (stored in DB, defaults below)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting

DEFAULT_WELCOME = (
    "<b>SPIRITTVPN</b>\n\n"
    "Твоя свобода в сети.\n\n"
    "Управляй подпиской, устройствами и VPN-подключением в одном месте."
)

DEFAULTS: dict[str, str] = {
    "welcome_text": DEFAULT_WELCOME,
    "trial_enabled": "1",
    "trial_days": "3",
    "notifications_enabled": "1",
    "maintenance": "0",
    "maintenance_text": "🛠 SpirittVPN на техническом обслуживании. Скоро вернёмся — попробуй чуть позже.",
    "support_extra_url": "",
    "referral_enabled": "1",
    "referral_trial_days": "3",
    "referral_payment_days": "7",
}

BOOL_KEYS = {"trial_enabled", "notifications_enabled", "maintenance", "referral_enabled"}
INT_KEYS = {"trial_days": (1, 30), "referral_trial_days": (0, 90), "referral_payment_days": (0, 90)}
MAX_TEXT = {"welcome_text": 900, "maintenance_text": 900, "support_extra_url": 200}


class SettingError(ValueError):
    pass


def normalize(key: str, value: str) -> str:
    if key not in DEFAULTS:
        raise SettingError(f"Неизвестный ключ. Доступно: {', '.join(DEFAULTS)}")
    value = value.strip()
    if key in BOOL_KEYS:
        if value.lower() in {"1", "on", "true", "да", "вкл"}:
            return "1"
        if value.lower() in {"0", "off", "false", "нет", "выкл"}:
            return "0"
        raise SettingError("Ожидается on/off")
    if key in INT_KEYS:
        lo, hi = INT_KEYS[key]
        if not value.isdigit() or not lo <= int(value) <= hi:
            raise SettingError(f"Ожидается число {lo}–{hi}")
    if key == "support_extra_url" and value and not value.startswith("https://"):
        raise SettingError("Ссылка должна начинаться с https://")
    if key in MAX_TEXT and len(value) > MAX_TEXT[key]:
        raise SettingError(f"Слишком длинно (макс. {MAX_TEXT[key]})")
    return value


async def get_setting(session: AsyncSession, key: str) -> str:
    row = await session.get(Setting, key)
    return row.value if row else DEFAULTS[key]


async def get_bool(session: AsyncSession, key: str) -> bool:
    return (await get_setting(session, key)) == "1"


async def get_int(session: AsyncSession, key: str) -> int:
    return int(await get_setting(session, key))


async def set_setting(session: AsyncSession, key: str, value: str) -> str:
    value = normalize(key, value)
    row = await session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    return value


async def all_settings(session: AsyncSession) -> dict[str, str]:
    stored = {s.key: s.value for s in (await session.scalars(select(Setting))).all()}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}

"""Single source of truth for brand assets, mandatory links, contacts and team roles."""
from __future__ import annotations

from pathlib import Path

PROJECT_NAME = "SpirittVPN"
SLOGAN = "твоя свобода в сети"

LOGO_PATH = Path(__file__).parent / "assets" / "spirittvpn_logo.png"

PRIVACY_POLICY_URL = "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
USER_AGREEMENT_URL = "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"

OWNER_USERNAME = "TTwer4"
DEVELOPER_USERNAME = "cupsov"
SUPPORT_USERNAMES: tuple[str, ...] = ("cupsov", "TTwer4")


def tg_url(username: str) -> str:
    return f"https://t.me/{username.lstrip('@')}"


def tg_link_html(username: str) -> str:
    username = username.lstrip("@")
    return f'<a href="{tg_url(username)}">@{username}</a>'

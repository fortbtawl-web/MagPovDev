from __future__ import annotations

from app.config import Settings
from app.vpn.base import VPNProvider
from app.vpn.dev_providers import NotConfiguredVPNProvider, TestVPNProvider


def build_vpn_provider(settings: Settings) -> VPNProvider:
    if settings.vpn_provider == "test":
        return TestVPNProvider()
    if settings.vpn_provider == "marzban":
        from app.vpn.marzban import MarzbanVPNProvider

        assert settings.marzban_url and settings.marzban_username and settings.marzban_password
        return MarzbanVPNProvider(
            settings.marzban_url,
            settings.marzban_username,
            settings.marzban_password.get_secret_value(),
            proxies=settings.marzban_proxies,
            username_prefix=settings.marzban_username_prefix,
            timeout=settings.vpn_timeout_seconds,
        )
    return NotConfiguredVPNProvider()

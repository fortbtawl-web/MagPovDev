from __future__ import annotations

from app.config import Settings
from app.vpn.base import VPNService
from app.vpn.mock import MockVPNService, NotConfiguredVPNService


def build_vpn_service(settings: Settings) -> VPNService:
    if settings.vpn_provider == "mock":
        return MockVPNService()
    if settings.vpn_provider == "remnawave":
        from app.vpn.remnawave import RemnawaveClient, RemnawaveVPNService

        assert settings.remnawave_url and settings.remnawave_token
        client = RemnawaveClient(
            settings.remnawave_url, settings.remnawave_token.get_secret_value(),
            timeout=settings.vpn_timeout_seconds, attempts=settings.vpn_retry_attempts, local=settings.remnawave_local,
        )
        squads = [s.strip() for s in settings.remnawave_squads.split(",") if s.strip()]
        return RemnawaveVPNService(client, squads=squads, hwid_limit=settings.remnawave_hwid_limit)
    if settings.vpn_provider == "marzban":
        from app.vpn.marzban import MarzbanVPNService

        assert settings.marzban_url and settings.marzban_username and settings.marzban_password
        return MarzbanVPNService(
            settings.marzban_url, settings.marzban_username, settings.marzban_password.get_secret_value(),
            proxies=settings.marzban_proxies, timeout=settings.vpn_timeout_seconds,
        )
    return NotConfiguredVPNService()

from __future__ import annotations

from urllib.parse import urlsplit

from backend.config import Settings
from backend.integrations.vpn.base import VpnClientInfo, VpnError, VpnHealth, VpnNotConfigured, VpnProvider
from backend.integrations.vpn.h1federated import H1FederatedProvider
from backend.integrations.vpn.h1vless import H1VlessProvider


class VpnRegistry:
    """Maps node codes to provider adapters."""

    def __init__(self, providers: dict[str, VpnProvider] | None = None):
        self._providers: dict[str, VpnProvider] = dict(providers or {})

    @classmethod
    def from_settings(cls, settings: Settings) -> VpnRegistry:
        providers: dict[str, VpnProvider] = {}
        specs = settings.node_specs()
        for spec in specs:
            if spec.api_url.startswith("federation://"):
                continue
            providers[spec.code] = H1VlessProvider(
                spec.code,
                spec.api_url,
                settings.node_token(spec.code),
                verify_tls=settings.vpn_verify_tls,
                timeout=settings.vpn_timeout_seconds,
                block_on_dead_endpoints=settings.vpn_block_on_dead_endpoints,
            )
        for spec in specs:
            if not spec.api_url.startswith("federation://"):
                continue
            hub_code = spec.api_url.removeprefix("federation://")
            hub = providers.get(hub_code)
            if isinstance(hub, H1VlessProvider):
                # Members: the other nodes that have a token; their host (minus the direct- prefix) is the node name.
                hosts = {
                    (urlsplit(sp.api_url).hostname or "").removeprefix("direct-")
                    for sp in specs
                    if sp.code not in (hub_code, spec.code) and settings.node_token(sp.code) and not sp.api_url.startswith("federation://")
                }
                providers[spec.code] = H1FederatedProvider(spec.code, hub, hosts)
        return cls(providers)

    def get(self, code: str) -> VpnProvider:
        provider = self._providers.get(code)
        if provider is None:
            raise VpnNotConfigured(f"Нода {code} не настроена в VPN_NODES")
        return provider

    def codes(self) -> list[str]:
        return list(self._providers)

    async def close(self) -> None:
        for p in self._providers.values():
            await p.close()


__all__ = ["VpnRegistry", "VpnProvider", "VpnError", "VpnNotConfigured", "VpnClientInfo", "VpnHealth"]

"""Adapter for the H1//VLESS node API (H1Cloud).

Contract verified against the node source (github.com/h1gurodev/h1cloud-vless, main.sh, `Handler`):
  base URL: https://<node>:<api_port>/api      auth: `Authorization: Bearer <api token>`
  GET    /health                       -> {"ok": true, "service": "vpn-api", ...}
  GET    /clients                      -> {"ok": true, "clients": [client, ...]}
  GET    /clients/{name}               -> {"ok": true, "client": {...}} | 404 {"ok": false, "error": "user_not_found"}
  POST   /create {name, days, device_limit}             -> 201 {"ok": true, "client": {...}} | 409 user_already_exists
  PATCH  /edit   {name, expires_at:int unix seconds}    -> {"ok": true, "client": {...}}
  PATCH  /clients/{name}/ban {reason} | /clients/{name}/unban
  DELETE /clients/{name}                -> {"ok": true, "deleted": name} | 404 user_not_found
  GET    /inbounds                      -> {"ok": true, "inbounds": [{enabled, public_host, public_port, ...}]}
Re-verified live against h1vless 2.7.3 panels (2026-10): the API is served on the panel port
(http://<host>:<panel_port>/api), the Bearer token is the panel API token (stable across logins,
also accepted as X-API-Key), and per-inbound VLESS links come in `inbound_links`, not `link`.
Client payload fields used: uuid, name, status ("active"|"banned"), expires_at (unix), subscription_url,
sub_url, link, device_limit, devices_count, online. Names must match ^[A-Za-z0-9._-]+$.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

import httpx

from backend.integrations.vpn.base import VpnClientInfo, VpnError, VpnHealth, VpnNotConfigured
from backend.utils import utcnow

log = logging.getLogger("spiritt.vpn.h1")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _inbound_link(data: dict[str, Any]) -> str | None:
    """h1vless 2.7.x: links live in `inbound_links`; prefer plain TCP (widest client support)."""
    items = [i for i in data.get("inbound_links") or [] if isinstance(i, dict) and str(i.get("link") or "").startswith("vless://")]
    items.sort(key=lambda i: 0 if i.get("network") == "tcp" else 1)
    return items[0]["link"] if items else None


async def tcp_probe(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


def _parse_client(data: dict[str, Any]) -> VpnClientInfo:
    exp_raw = data.get("expires_at")
    expires = None
    if isinstance(exp_raw, (int, float)) and exp_raw > 0:
        expires = datetime.fromtimestamp(int(exp_raw), tz=timezone.utc)
    link = data.get("link") or None
    if not link:
        links = data.get("links")
        if isinstance(links, dict):
            link = next((v for v in links.values() if isinstance(v, str) and v.startswith("vless://")), None)
        elif isinstance(links, list):
            link = next((v for v in links if isinstance(v, str) and v.startswith("vless://")), None)
    link = link or _inbound_link(data)
    return VpnClientInfo(
        name=str(data.get("name") or ""),
        uuid=str(data["uuid"]) if data.get("uuid") else None,
        banned=str(data.get("status") or "").lower() == "banned",
        expires_at=expires,
        subscription_url=(data.get("subscription_url") or data.get("sub_url") or None),
        link=link,
        device_limit=data.get("device_limit") if isinstance(data.get("device_limit"), int) else None,
        devices_count=data.get("devices_count") if isinstance(data.get("devices_count"), int) else None,
        online=data.get("online") if isinstance(data.get("online"), bool) else None,
    )


class H1VlessProvider:
    def __init__(
        self,
        code: str,
        api_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        endpoint_probe: Callable[[str, int], Awaitable[bool]] | None = tcp_probe,
        block_on_dead_endpoints: bool = False,
    ):
        self.code = code
        self._probe = endpoint_probe
        self._block = block_on_dead_endpoints
        self._base = api_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            verify=verify_tls,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._base and self._token)

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        if not self.configured:
            raise VpnNotConfigured(f"Нода {self.code}: не задан URL или токен API")
        try:
            resp = await self._client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise VpnError(f"Нода {self.code}: таймаут", code="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise VpnError(f"Нода {self.code}: сеть недоступна ({type(exc).__name__})", code="unreachable", retryable=True) from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise VpnError(
                f"Нода {self.code}: некорректный ответ (HTTP {resp.status_code})",
                code="bad_response",
                retryable=resp.status_code >= 500,
                status=resp.status_code,
            )
        if resp.status_code == 401 or data.get("error") in ("unauthorized", "bad_token"):
            raise VpnError(f"Нода {self.code}: неверный API-токен", code="unauthorized", status=401)
        return resp.status_code, data

    def _raise_for(self, status: int, data: dict[str, Any]) -> None:
        if data.get("ok"):
            return
        code = str(data.get("error") or "unknown")
        raise VpnError(f"Нода {self.code}: {code}", code=code, retryable=status >= 500, status=status)

    @staticmethod
    def _check_name(name: str) -> str:
        if not NAME_RE.match(name):
            raise VpnError(f"Недопустимое имя клиента {name!r}", code="bad_name")
        return name

    async def health_check(self) -> VpnHealth:
        try:
            status, data = await self._request("GET", "/health")
            if not data.get("ok"):
                return VpnHealth(ok=False, detail=str(data.get("error") or f"HTTP {status}"))
            # /health is unauthenticated on the node; /clients proves the token works.
            status, cdata = await self._request("GET", "/clients")
            self._raise_for(status, cdata)
            clients = cdata.get("clients")
            count = len(clients) if isinstance(clients, list) else None
            # A healthy panel can still hand out links to a dead relay; check the VPN ports themselves.
            down = await self._unreachable_endpoints()
            if down:
                # The probe runs from the bot's network, which may not reach the public relay that clients use,
                # so by default it only warns; set VPN_BLOCK_ON_DEAD_ENDPOINTS=true to treat it as an outage.
                detail = f"VPN-порты не отвечают с сервера бота: {', '.join(down)}"
                return VpnHealth(ok=not self._block, detail=detail, clients=count)
            return VpnHealth(ok=True, detail="ok", clients=count)
        except VpnError as exc:
            return VpnHealth(ok=False, detail=str(exc))

    async def _unreachable_endpoints(self) -> list[str]:
        """Public VLESS endpoints that refuse TCP. Empty if any endpoint works or none are listed."""
        if self._probe is None:
            return []
        status, data = await self._request("GET", "/inbounds")
        if status != 200 or not data.get("ok"):
            return []
        targets = [
            (str(i["public_host"]), int(i["public_port"]))
            for i in data.get("inbounds") or []
            if isinstance(i, dict) and i.get("enabled", True) and i.get("public_host") and str(i.get("public_port") or "").isdigit()
        ]
        if not targets:
            return []
        results = await asyncio.gather(*(self._probe(h, p) for h, p in targets))
        if any(results):
            return []
        return [f"{h}:{p}" for h, p in targets]

    async def get_client(self, name: str) -> VpnClientInfo | None:
        status, data = await self._request("GET", f"/clients/{quote(self._check_name(name), safe='')}")
        if status == 404 and data.get("error") == "user_not_found":
            return None
        self._raise_for(status, data)
        client = data.get("client")
        if not isinstance(client, dict):
            raise VpnError(f"Нода {self.code}: ответ без client", code="bad_response")
        return _parse_client(client)

    async def create_client(
        self, name: str, *, expires_at: datetime, device_limit: int, client_uuid: str | None = None
    ) -> VpnClientInfo:
        self._check_name(name)
        days = max(1, math.ceil((expires_at - utcnow()).total_seconds() / 86400))
        payload: dict[str, Any] = {"name": name, "days": days}
        if device_limit > 0:
            payload["device_limit"] = device_limit
        if client_uuid:
            payload["uuid"] = client_uuid
        status, data = await self._request("POST", "/create", json=payload)
        if status == 409 and data.get("error") == "user_already_exists":
            # Idempotent retry: the client was created by an earlier attempt.
            existing = await self.get_client(name)
            if existing is None:
                raise VpnError(f"Нода {self.code}: конфликт имени {name}", code="conflict", retryable=True)
        else:
            self._raise_for(status, data)
        # `days` is rounded up by the node; pin the exact expiry.
        return await self.extend_client(name, expires_at=expires_at)

    async def extend_client(self, name: str, *, expires_at: datetime) -> VpnClientInfo:
        payload = {"name": self._check_name(name), "expires_at": int(expires_at.timestamp())}
        status, data = await self._request("PATCH", "/edit", json=payload)
        self._raise_for(status, data)
        client = data.get("client")
        return _parse_client(client) if isinstance(client, dict) else await self._must_get(name)

    async def _must_get(self, name: str) -> VpnClientInfo:
        info = await self.get_client(name)
        if info is None:
            raise VpnError(f"Нода {self.code}: клиент {name} не найден", code="user_not_found")
        return info

    async def suspend_client(self, name: str, *, reason: str) -> None:
        status, data = await self._request(
            "PATCH", f"/clients/{quote(self._check_name(name), safe='')}/ban", json={"reason": reason[:200]}
        )
        self._raise_for(status, data)

    async def resume_client(self, name: str) -> None:
        status, data = await self._request("PATCH", f"/clients/{quote(self._check_name(name), safe='')}/unban")
        self._raise_for(status, data)

    async def revoke_client(self, name: str) -> None:
        status, data = await self._request("DELETE", f"/clients/{quote(self._check_name(name), safe='')}")
        if status == 404 and data.get("error") == "user_not_found":
            return
        self._raise_for(status, data)

    async def get_subscription_link(self, name: str) -> str | None:
        info = await self.get_client(name)
        return info.best_link if info else None

    async def get_client_status(self, name: str) -> str:
        info = await self.get_client(name)
        if info is None:
            return "missing"
        if info.banned:
            return "banned"
        if info.expires_at and info.expires_at <= utcnow():
            return "expired"
        return "active"

    async def close(self) -> None:
        await self._client.aclose()

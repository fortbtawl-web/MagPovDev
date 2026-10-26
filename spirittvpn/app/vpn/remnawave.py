"""Remnawave panel client, written against the official contract (remnawave/backend libs/contract, v3.4.15).

  POST   /api/users                         create   {username, expireAt, status, telegramId, hwidDeviceLimit, activeInternalSquads, description}
  GET    /api/users/{id}                    get by numeric id
  GET    /api/users/by-username/{username}  get by username (used to recover from lost create responses)
  PATCH  /api/users                         update   {id, expireAt, status: ACTIVE|DISABLED}
  DELETE /api/users/{id}                    delete (revokes access)
  GET    /api/nodes                         node list {uuid, name, address, countryCode, isConnected, isDisabled, usersOnline, lastStatusMessage}
All responses are wrapped as {"response": ...}; auth is `Authorization: Bearer <API token>`.
Verify against /backend-tools/scalar of your panel version before going live.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import aiohttp

from app.vpn.base import VPNClient, VPNConnection, VPNError, VPNNodeInfo, VPNService, VPNUnavailableError

log = logging.getLogger("spirittvpn.vpn.remnawave")


class RemnawaveClient:
    """Thin async HTTP client. Retries only idempotent requests."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0, attempts: int = 3,
                 local: bool = False, base_delay: float = 0.5) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if local:
            # Remnawave rejects plain-HTTP API calls unless they look proxied (ProxyCheckGuard).
            self._headers |= {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._attempts = attempts
        self._base_delay = base_delay
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)
        return self._session

    async def request(self, method: str, path: str, json: dict | None = None, *, idempotent: bool = True):
        attempts = self._attempts if idempotent else 1
        for attempt in range(1, attempts + 1):
            try:
                return await self._once(method, path, json)
            except VPNUnavailableError:
                if attempt == attempts:
                    raise
                await asyncio.sleep(self._base_delay * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    async def _once(self, method: str, path: str, json: dict | None):
        session = await self._http()
        try:
            async with session.request(method, f"{self._base}{path}", json=json) as resp:
                if resp.status == 404:
                    return None
                if resp.status >= 500 or resp.status == 429:
                    raise VPNUnavailableError(f"remnawave http {resp.status}")
                if resp.status in (401, 403):
                    raise VPNError(f"remnawave auth rejected (http {resp.status})")
                if resp.status >= 400:
                    raise VPNError(f"remnawave {method} http {resp.status}")
                if resp.content_type == "application/json":
                    data = await resp.json()
                    return data.get("response", data) if isinstance(data, dict) else data
                return {}
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VPNUnavailableError(type(exc).__name__) from exc

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class RemnawaveVPNService(VPNService):
    name = "remnawave"

    def __init__(self, client: RemnawaveClient, *, squads: list[str] | None = None, hwid_limit: int = 1) -> None:
        self.client = client
        self.squads = squads or []
        self.hwid_limit = hwid_limit

    @staticmethod
    def _to_client(data: dict) -> VPNClient:
        return VPNClient(
            external_id=str(data["id"]),
            username=data["username"],
            enabled=data.get("status") == "ACTIVE",
            expires_at=_parse_dt(data.get("expireAt")),
        )

    async def create_client(self, username, telegram_id, expires_at, device_limit=None):
        body: dict = {
            "username": username,
            "status": "ACTIVE",
            "expireAt": _iso(expires_at),
            "trafficLimitBytes": 0,
            "telegramId": telegram_id,
            "description": "SpirittVPN bot",
        }
        if self.hwid_limit:
            body["hwidDeviceLimit"] = self.hwid_limit
        if self.squads:
            body["activeInternalSquads"] = self.squads
        data = await self.client.request("POST", "/api/users", body, idempotent=False)
        if not data:
            raise VPNError("empty create response")
        log.info("remnawave: created client for tg=%s", telegram_id)
        return self._to_client(data)

    async def find_client(self, username):
        data = await self.client.request("GET", f"/api/users/by-username/{username}")
        return self._to_client(data) if data else None

    async def get_client(self, external_id):
        data = await self.client.request("GET", f"/api/users/{external_id}")
        return self._to_client(data) if data else None

    async def update_client(self, external_id, *, expires_at, enabled):
        data = await self.client.request(
            "PATCH", "/api/users",
            {"id": int(external_id), "expireAt": _iso(expires_at), "status": "ACTIVE" if enabled else "DISABLED"},
        )
        if data is None:
            raise VPNError("client not found")
        return self._to_client(data)

    async def revoke_client(self, external_id):
        await self.client.request("DELETE", f"/api/users/{external_id}")
        log.info("remnawave: deleted client id=%s", external_id)

    async def get_connection(self, external_id):
        data = await self.client.request("GET", f"/api/users/{external_id}")
        if not data or data.get("status") != "ACTIVE":
            raise VPNError("client is not active")
        url = data.get("subscriptionUrl")
        if not url:
            raise VPNError("no subscription url")
        return VPNConnection(link=url)

    async def list_nodes(self):
        data = await self.client.request("GET", "/api/nodes") or []
        return [
            VPNNodeInfo(
                external_id=str(n["uuid"]), name=n.get("name", ""), address=n.get("address", ""),
                country=n.get("countryCode", ""), is_connected=bool(n.get("isConnected")),
                is_disabled=bool(n.get("isDisabled")), users_online=int(n.get("usersOnline") or 0),
                status_message=n.get("lastStatusMessage"),
            )
            for n in data
        ]

    async def close(self) -> None:
        await self.client.close()

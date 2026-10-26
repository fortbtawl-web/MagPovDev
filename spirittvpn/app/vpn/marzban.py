"""Marzban panel adapter.

Endpoints follow the official Marzban source (app/routers/admin.py, app/routers/user.py):
  POST /api/admin/token            (OAuth2 password form) -> {"access_token": ...}
  POST /api/user                   UserCreate {username, proxies, expire (unix ts), status}
  GET  /api/user/{username}        UserResponse {status, expire, subscription_url, links, ...}
  PUT  /api/user/{username}        UserModify {expire, status: active|disabled|on_hold}
  DELETE /api/user/{username}
Verify against the /docs page of your own panel version before going live.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import aiohttp

from app.vpn.base import VPNClient, VPNConnection, VPNError, VPNService, VPNUnavailableError

log = logging.getLogger("spirittvpn.vpn.marzban")


class MarzbanVPNService(VPNService):
    name = "marzban"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        proxies: str = "vless",
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._admin_user = username
        self._admin_password = password
        self._proxies = [p.strip() for p in proxies.split(",") if p.strip()]
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _login(self) -> None:
        session = await self._http()
        try:
            async with session.post(
                f"{self._base}/api/admin/token",
                data={"username": self._admin_user, "password": self._admin_password},
            ) as resp:
                if resp.status >= 500:
                    raise VPNUnavailableError(f"marzban auth http {resp.status}")
                if resp.status != 200:
                    raise VPNError(f"marzban auth rejected (http {resp.status})")
                self._token = (await resp.json())["access_token"]
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VPNUnavailableError(type(exc).__name__) from exc

    async def _request(self, method: str, path: str, json: dict | None = None, *, retry_auth: bool = True):
        attempts = 1 if method == "POST" else 3  # only idempotent requests are retried
        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(method, path, json, retry_auth=retry_auth)
            except VPNUnavailableError:
                if attempt == attempts:
                    raise
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))

    async def _request_once(self, method: str, path: str, json: dict | None = None, *, retry_auth: bool = True):
        if self._token is None:
            await self._login()
        session = await self._http()
        try:
            async with session.request(
                method, f"{self._base}{path}", json=json, headers={"Authorization": f"Bearer {self._token}"}
            ) as resp:
                if resp.status == 401 and retry_auth:
                    self._token = None
                    return await self._request_once(method, path, json, retry_auth=False)
                if resp.status == 404:
                    return None
                if resp.status >= 500:
                    raise VPNUnavailableError(f"marzban http {resp.status}")
                if resp.status >= 400:
                    raise VPNError(f"marzban {method} {path.split('/')[2]} http {resp.status}")
                if resp.content_type == "application/json":
                    return await resp.json()
                return {}
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VPNUnavailableError(type(exc).__name__) from exc

    @staticmethod
    def _to_client(data: dict) -> VPNClient:
        expire = data.get("expire")
        return VPNClient(
            external_id=data["username"], username=data["username"], enabled=data.get("status") == "active",
            expires_at=datetime.fromtimestamp(expire, UTC) if expire else None,
        )

    async def create_client(self, username, telegram_id, expires_at, device_limit=None):
        data = await self._request("POST", "/api/user", {
            "username": username, "proxies": {p: {} for p in self._proxies}, "expire": int(expires_at.timestamp()),
            "data_limit": 0, "status": "active", "note": f"telegram:{telegram_id}",
        })
        if not data:
            raise VPNError("empty create response")
        return self._to_client(data)

    async def find_client(self, username):
        data = await self._request("GET", f"/api/user/{username}")
        return self._to_client(data) if data else None

    async def get_client(self, external_id):
        return await self.find_client(external_id)

    async def update_client(self, external_id, *, expires_at, enabled):
        data = await self._request("PUT", f"/api/user/{external_id}", {
            "expire": int(expires_at.timestamp()), "status": "active" if enabled else "disabled"})
        if data is None:
            raise VPNError("client not found")
        return self._to_client(data)

    async def revoke_client(self, external_id):
        await self._request("DELETE", f"/api/user/{external_id}")

    async def get_connection(self, external_id):
        data = await self._request("GET", f"/api/user/{external_id}")
        if not data or data.get("status") != "active":
            raise VPNError("client is not active")
        sub_url = data.get("subscription_url") or ""
        if sub_url.startswith("/"):
            sub_url = f"{self._base}{sub_url}"
        if not sub_url:
            raise VPNError("no subscription url")
        return VPNConnection(link=sub_url)

    async def list_nodes(self):
        return []  # node API not integrated for Marzban

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

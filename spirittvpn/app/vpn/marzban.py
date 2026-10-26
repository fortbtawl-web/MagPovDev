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

import logging
from datetime import UTC, datetime

import aiohttp

from app.vpn.base import VPNClientState, VPNConnection, VPNError, VPNProvider, VPNUnavailableError

log = logging.getLogger("spirittvpn.vpn.marzban")


class MarzbanVPNProvider(VPNProvider):
    name = "marzban"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        proxies: str = "vless",
        username_prefix: str = "spiritt_",
        timeout: float = 10.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._admin_user = username
        self._admin_password = password
        self._proxies = [p.strip() for p in proxies.split(",") if p.strip()]
        self._prefix = username_prefix
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None

    def _username(self, telegram_id: int) -> str:
        return f"{self._prefix}{telegram_id}"

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
        if self._token is None:
            await self._login()
        session = await self._http()
        try:
            async with session.request(
                method, f"{self._base}{path}", json=json, headers={"Authorization": f"Bearer {self._token}"}
            ) as resp:
                if resp.status == 401 and retry_auth:
                    self._token = None
                    return await self._request(method, path, json, retry_auth=False)
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
    def _state(data: dict) -> VPNClientState:
        expire = data.get("expire")
        return VPNClientState(
            external_id=data["username"],
            enabled=data.get("status") == "active",
            expires_at=datetime.fromtimestamp(expire, UTC) if expire else None,
        )

    async def ensure_client(self, telegram_id: int, expires_at: datetime) -> VPNClientState:
        username = self._username(telegram_id)
        expire = int(expires_at.timestamp())
        existing = await self._request("GET", f"/api/user/{username}")
        if existing is None:
            data = await self._request(
                "POST",
                "/api/user",
                {
                    "username": username,
                    "proxies": {p: {} for p in self._proxies},
                    "expire": expire,
                    "data_limit": 0,
                    "status": "active",
                    "note": f"telegram:{telegram_id}",
                },
            )
            log.info("marzban: created client for tg=%s", telegram_id)
        else:
            data = await self._request("PUT", f"/api/user/{username}", {"expire": expire, "status": "active"})
            log.info("marzban: updated client for tg=%s", telegram_id)
        return self._state(data)

    async def suspend(self, telegram_id: int) -> None:
        if await self._request("PUT", f"/api/user/{self._username(telegram_id)}", {"status": "disabled"}) is None:
            log.info("marzban: suspend skipped, no client for tg=%s", telegram_id)

    async def revoke(self, telegram_id: int) -> None:
        await self._request("DELETE", f"/api/user/{self._username(telegram_id)}")
        log.info("marzban: revoked client for tg=%s", telegram_id)

    async def get_state(self, telegram_id: int) -> VPNClientState | None:
        data = await self._request("GET", f"/api/user/{self._username(telegram_id)}")
        return self._state(data) if data else None

    async def get_connection(self, telegram_id: int) -> VPNConnection:
        data = await self._request("GET", f"/api/user/{self._username(telegram_id)}")
        if not data or data.get("status") != "active":
            raise VPNError("client is not active")
        sub_url = data.get("subscription_url") or ""
        if sub_url.startswith("/"):
            sub_url = f"{self._base}{sub_url}"
        if sub_url:
            return VPNConnection(link=sub_url)
        links = data.get("links") or []
        if links:
            return VPNConnection(config_text="\n".join(links), config_filename="spirittvpn.txt")
        raise VPNError("no connection data returned")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

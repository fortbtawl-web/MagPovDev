"""One subscription across several H1 nodes ("all locations" + client-side auto-select).

The hub is the federation main (verified live on h1vless 2.7.3). It creates the client on each linked node through
`/fed/lproxy/<H1Cloud server id>/create` with the same UUID plus `/fed/origin`; after that expiry, ban/unban and
delete done on the hub propagate to the linked nodes by themselves, and the hub's `sub_url` lists every location.
Only the hub's API token is needed. Linked nodes are best effort: the hub alone is enough to hand out a key, and
missing members are re-created when the client is read (`get_client`).
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid as uuidlib
from datetime import datetime

from backend.integrations.vpn.base import VpnClientInfo, VpnError, VpnHealth
from backend.integrations.vpn.h1vless import H1VlessProvider
from backend.utils import utcnow

log = logging.getLogger("spiritt.vpn.h1fed")
ORIGIN_NAME = "основная"


class H1FederatedProvider:
    def __init__(self, code: str, hub: H1VlessProvider, member_hosts: set[str], *, heal_interval: float = 600.0):
        self.code = code
        self._hub = hub
        self._hosts = {h.lower() for h in member_hosts}
        self._heal_interval = heal_interval
        self._members_cache: tuple[float, dict[str, str]] | None = None
        self._healed: dict[str, float] = {}

    async def _members(self) -> dict[str, str]:
        """{H1Cloud server id: host} of linked nodes we are configured to use and that answer through the hub."""
        now = time.monotonic()
        if self._members_cache and now < self._members_cache[0]:
            return self._members_cache[1]
        status, data = await self._hub._request("GET", "/fed/link")
        ids = [str(i) for i in data.get("links") or []] if status == 200 and data.get("ok") else []
        found: dict[str, str] = {}

        async def probe(server_id: str) -> None:
            try:
                st, d = await self._hub._request("GET", f"/fed/lproxy/{server_id}/status")
            except VpnError:
                return
            host = str(d.get("node_name") or d.get("domain") or "").lower()
            if st == 200 and host in self._hosts:
                found[server_id] = host

        await asyncio.gather(*(probe(i) for i in ids))
        self._members_cache = (now + (600 if found else 60), found)
        return found

    async def _create_on_member(self, server_id: str, name: str, uid: str, expires_at: datetime, device_limit: int) -> bool:
        days = max(1, math.ceil((expires_at - utcnow()).total_seconds() / 86400))
        payload: dict = {"name": name, "days": days, "manual": 1, "uuid": uid}
        if device_limit > 0:
            payload["device_limit"] = device_limit
        try:
            status, data = await self._hub._request("POST", f"/fed/lproxy/{server_id}/create", json=payload)
            exists = status == 409 or "already_exists" in str(data.get("error") or "")
            if not data.get("ok") and not exists:
                log.warning("member %s create failed: %s", server_id, data.get("error"))
                return False
            await self._hub._request("POST", f"/fed/lproxy/{server_id}/fed/origin", json={"uuid": uid, "name": ORIGIN_NAME})
            return True
        except VpnError as exc:
            log.warning("member %s create failed: %s", server_id, exc.code)
            return False

    async def _ensure_members(self, name: str, uid: str, expires_at: datetime, device_limit: int) -> None:
        try:
            members = await self._members()
        except VpnError as exc:
            log.warning("members unavailable: %s", exc.code)
            return
        await asyncio.gather(*(self._create_on_member(i, name, uid, expires_at, device_limit) for i in members))

    async def health_check(self) -> VpnHealth:
        health = await self._hub.health_check()
        if not health.ok:
            return health
        try:
            members = await self._members()
        except VpnError:
            members = {}
        if len(members) < len(self._hosts):
            return VpnHealth(ok=True, detail=f"в подписку входит {len(members) + 1} из {len(self._hosts) + 1} локаций", clients=health.clients)
        return VpnHealth(ok=True, detail="ok", clients=health.clients)

    async def create_client(self, name: str, *, expires_at: datetime, device_limit: int) -> VpnClientInfo:
        existing = await self._hub.get_client(name)
        uid = existing.uuid if existing and existing.uuid else str(uuidlib.uuid4())
        await self._hub.create_client(name, expires_at=expires_at, device_limit=device_limit, client_uuid=uid)
        await self._ensure_members(name, uid, expires_at, device_limit)
        # Pin the exact expiry once more: the hub pushes it to the members that now exist.
        return await self._hub.extend_client(name, expires_at=expires_at)

    async def get_client(self, name: str) -> VpnClientInfo | None:
        info = await self._hub.get_client(name)
        if info is not None:
            await self._heal(name, info)
        return info

    async def _heal(self, name: str, info: VpnClientInfo) -> None:
        now = time.monotonic()
        if info.banned or not info.uuid or not info.expires_at or info.expires_at <= utcnow():
            return
        if now - self._healed.get(name, -1e9) < self._heal_interval:
            return
        self._healed[name] = now
        try:
            members = await self._members()

            async def one(server_id: str) -> None:
                status, data = await self._hub._request("GET", f"/fed/lproxy/{server_id}/clients/{name}")
                if status == 404 or (status == 200 and not data.get("client")):
                    await self._create_on_member(server_id, name, info.uuid, info.expires_at, info.device_limit or 0)

            await asyncio.gather(*(one(i) for i in members), return_exceptions=True)
        except VpnError as exc:
            log.warning("heal skipped: %s", exc.code)

    async def extend_client(self, name: str, *, expires_at: datetime) -> VpnClientInfo:
        return await self._hub.extend_client(name, expires_at=expires_at)

    async def suspend_client(self, name: str, *, reason: str) -> None:
        await self._hub.suspend_client(name, reason=reason)

    async def resume_client(self, name: str) -> None:
        await self._hub.resume_client(name)

    async def revoke_client(self, name: str) -> None:
        await self._hub.revoke_client(name)
        self._healed.pop(name, None)
        try:
            members = await self._members()
        except VpnError:
            return
        for server_id in members:  # normally already removed by the hub; make sure
            try:
                await self._hub._request("DELETE", f"/fed/lproxy/{server_id}/clients/{name}")
            except VpnError as exc:
                log.warning("member %s delete failed: %s", server_id, exc.code)

    async def get_subscription_link(self, name: str) -> str | None:
        return await self._hub.get_subscription_link(name)

    async def get_client_status(self, name: str) -> str:
        return await self._hub.get_client_status(name)

    async def close(self) -> None:
        """The hub is closed by the registry together with the other providers."""

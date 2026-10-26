"""RemnawaveClient against a local fake panel implementing the documented contract."""
from datetime import UTC, datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.services.devices import create_with_recovery
from app.vpn.base import VPNError, VPNUnavailableError
from app.vpn.remnawave import RemnawaveClient, RemnawaveVPNService

TOKEN = "rw-token"
EXP = datetime(2026, 3, 1, tzinfo=UTC)


class FakePanel:
    def __init__(self):
        self.users: dict[int, dict] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_get = 0
        self.lose_create_response = False
        self.next_id = 1

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        return app

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append((request.method, request.path))
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"message": "unauthorized"}, status=401)
        path, method = request.path, request.method
        if method == "POST" and path == "/api/users":
            body = await request.json()
            assert set(body) >= {"username", "expireAt", "status"}
            user = {"id": self.next_id, "username": body["username"], "status": body["status"], "expireAt": body["expireAt"],
                    "subscriptionUrl": f"https://sub.example/{body['username']}", "hwidDeviceLimit": body.get("hwidDeviceLimit")}
            self.next_id += 1
            self.users[user["id"]] = user
            if self.lose_create_response:
                self.lose_create_response = False
                return web.Response(status=502)
            return web.json_response({"response": user}, status=201)
        if method == "PATCH" and path == "/api/users":
            body = await request.json()
            user = self.users.get(body["id"])
            if not user:
                return web.json_response({}, status=404)
            user.update({k: v for k, v in body.items() if k in ("status", "expireAt")})
            return web.json_response({"response": user})
        if path.startswith("/api/users/by-username/"):
            name = path.rsplit("/", 1)[1]
            user = next((u for u in self.users.values() if u["username"] == name), None)
            return web.json_response({"response": user}) if user else web.json_response({}, status=404)
        if path.startswith("/api/users/"):
            uid = int(path.rsplit("/", 1)[1])
            if method == "DELETE":
                self.users.pop(uid, None)
                return web.json_response({"response": {"isDeleted": True}})
            if self.fail_get:
                self.fail_get -= 1
                return web.Response(status=503)
            user = self.users.get(uid)
            return web.json_response({"response": user}) if user else web.json_response({}, status=404)
        if path == "/api/nodes":
            return web.json_response({"response": [
                {"uuid": "n1", "name": "Frankfurt", "address": "fra.example", "countryCode": "DE", "isConnected": True,
                 "isDisabled": False, "usersOnline": 3, "lastStatusMessage": None}]})
        return web.json_response({}, status=404)


@pytest.fixture
async def panel():
    fake = FakePanel()
    server = TestServer(fake.app())
    await server.start_server()
    client = RemnawaveClient(str(server.make_url("")), TOKEN, timeout=5, attempts=3, base_delay=0)
    yield fake, RemnawaveVPNService(client, squads=["00000000-0000-0000-0000-000000000001"], hwid_limit=1)
    await client.close()
    await server.close()


async def test_full_client_lifecycle(panel):
    fake, svc = panel
    c = await svc.create_client("sp_42_abc123", 42, EXP)
    assert c.enabled and c.expires_at == EXP and c.external_id == "1"
    assert fake.users[1]["hwidDeviceLimit"] == 1
    conn = await svc.get_connection(c.external_id)
    assert conn.link == "https://sub.example/sp_42_abc123" and not conn.is_test
    upd = await svc.update_client(c.external_id, expires_at=EXP, enabled=False)
    assert not upd.enabled
    with pytest.raises(VPNError):
        await svc.get_connection(c.external_id)
    await svc.revoke_client(c.external_id)
    assert await svc.get_client(c.external_id) is None
    nodes = await svc.list_nodes()
    assert nodes[0].name == "Frankfurt" and nodes[0].is_connected


async def test_idempotent_get_is_retried(panel):
    fake, svc = panel
    c = await svc.create_client("sp_1_aaaaaa", 1, EXP)
    fake.fail_get = 2
    assert (await svc.get_client(c.external_id)).username == "sp_1_aaaaaa"


async def test_create_is_not_retried_but_recovered_by_username(panel):
    fake, svc = panel
    fake.lose_create_response = True
    with pytest.raises(VPNUnavailableError):
        await svc.create_client("sp_2_bbbbbb", 2, EXP)
    assert [r for r in fake.requests if r[0] == "POST"] == [("POST", "/api/users")]
    fake.lose_create_response = True
    client = await create_with_recovery(svc, "sp_3_cccccc", 3, EXP)
    assert client.username == "sp_3_cccccc"
    assert len([u for u in fake.users.values() if u["username"] == "sp_3_cccccc"]) == 1


async def test_bad_token_is_not_retried():
    fake = FakePanel()
    server = TestServer(fake.app())
    await server.start_server()
    client = RemnawaveClient(str(server.make_url("")), "wrong", attempts=3, base_delay=0)
    try:
        with pytest.raises(VPNError) as exc:
            await RemnawaveVPNService(client).list_nodes()
        assert not isinstance(exc.value, VPNUnavailableError)
        assert len(fake.requests) == 1
    finally:
        await client.close()
        await server.close()


async def test_panel_down_is_transient():
    client = RemnawaveClient("http://127.0.0.1:9", TOKEN, timeout=1, attempts=2, base_delay=0)
    try:
        with pytest.raises(VPNUnavailableError):
            await RemnawaveVPNService(client).get_client("1")
    finally:
        await client.close()

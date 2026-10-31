"""Device management and VPN synchronisation."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from backend.integrations.vpn import VpnError
from backend.models import Device, Subscription, VpnOperation
from backend.services import admin as admin_svc
from backend.services import devices as dev_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import ServiceError
from backend.utils import utcnow
from tests.conftest import ADMIN_ID, make_user


async def grant(ctx, uid, days=30, limit=1):
    await make_user(ctx, uid)
    return await admin_svc.add_days(ctx, ADMIN_ID, "web", uid, days, "тестовая подписка", device_limit=limit)


async def test_create_device(ctx):
    await grant(ctx, 1)
    res = await dev_svc.add_device(ctx, 1, node_code="de", label="Телефон")
    assert res.confirmed and res.device.status == "active" and res.device.label == "Телефон"
    assert res.device.subscription_url.startswith("https://de.node.test/sub/")
    assert res.device.client_name in ctx.extra["nodes"]["de"].clients


async def test_limit_respected_and_exceeded(ctx):
    await grant(ctx, 2, limit=2)
    assert (await dev_svc.add_device(ctx, 2)).confirmed
    assert (await dev_svc.add_device(ctx, 2)).confirmed
    with pytest.raises(ServiceError) as e:
        await dev_svc.add_device(ctx, 2)
    assert e.value.code == "device_limit" and "2 из 2" in e.value.message


async def test_parallel_adds_respect_limit(ctx):
    await grant(ctx, 3, limit=2)
    for n in ctx.extra["nodes"].values():
        n.delay = 0.05
    results = await asyncio.gather(*[dev_svc.add_device(ctx, 3) for _ in range(6)], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, ServiceError)]
    assert len(ok) == 2 and len(errors) == 4
    async with ctx.session() as s:
        assert await s.scalar(select(func.count(Device.id)).where(Device.user_id == 3)) == 2
    assert sum(len(n.clients) for n in ctx.extra["nodes"].values()) == 2


async def test_revoke_success(ctx):
    await grant(ctx, 4)
    d = (await dev_svc.add_device(ctx, 4, node_code="fi")).device
    res = await dev_svc.revoke_device(ctx, d.id, user_id=4)
    assert res.confirmed and res.device.status == "revoked"
    assert d.client_name not in ctx.extra["nodes"]["fi"].clients
    # Slot is free again.
    assert (await dev_svc.add_device(ctx, 4)).confirmed


async def test_revoke_other_users_device_forbidden(ctx):
    await grant(ctx, 5)
    await make_user(ctx, 6)
    d = (await dev_svc.add_device(ctx, 5)).device
    with pytest.raises(ServiceError):
        await dev_svc.revoke_device(ctx, d.id, user_id=6)
    assert d.client_name in ctx.extra["nodes"][d.node.code].clients


async def test_revoke_server_error_keeps_revoking(ctx):
    await grant(ctx, 7)
    d = (await dev_svc.add_device(ctx, 7, node_code="fi")).device
    node = ctx.extra["nodes"]["fi"]
    node.fail["revoke"] = VpnError("unreachable", code="unreachable", retryable=True)
    res = await dev_svc.revoke_device(ctx, d.id, user_id=7)
    assert not res.confirmed and res.device.status == "revoking"
    assert d.client_name in node.clients
    # Still occupies the slot until confirmed.
    with pytest.raises(ServiceError):
        await dev_svc.add_device(ctx, 7)
    node.fail.clear()
    async with ctx.session() as s, s.begin():
        await s.execute(update(VpnOperation).values(next_retry_at=utcnow()))
    await dev_svc.retry_pending(ctx)
    async with ctx.session() as s:
        assert (await s.get(Device, d.id)).status == "revoked"
    assert d.client_name not in node.clients


async def test_subscription_expiry_marks_devices(ctx):
    await grant(ctx, 8)
    d = (await dev_svc.add_device(ctx, 8)).device
    async with ctx.session() as s, s.begin():
        await s.execute(update(Subscription).where(Subscription.user_id == 8).values(expires_at=utcnow() - timedelta(minutes=1)))
    async with ctx.session() as s, s.begin():
        await subs_svc.expire_due(s)
    async with ctx.session() as s:
        assert (await s.get(Device, d.id)).status == "expired"
    with pytest.raises(ServiceError):
        await dev_svc.add_device(ctx, 8)


async def test_drift_missing_client_recreated(ctx):
    await grant(ctx, 9)
    d = (await dev_svc.add_device(ctx, 9, node_code="fi")).device
    node = ctx.extra["nodes"]["fi"]
    node.clients.clear()  # someone deleted it on the node
    async with ctx.session() as s, s.begin():
        await s.execute(update(Device).values(last_synced_at=None))
    stats = await dev_svc.reconcile(ctx)
    assert stats["recreated"] == 1
    assert d.client_name in node.clients
    async with ctx.session() as s:
        assert (await s.get(Device, d.id)).status == "active"


async def test_drift_expiry_fixed(ctx):
    sub = await grant(ctx, 10)
    d = (await dev_svc.add_device(ctx, 10, node_code="de")).device
    node = ctx.extra["nodes"]["de"]
    node.clients[d.client_name]["expires_at"] = utcnow() + timedelta(days=365)
    async with ctx.session() as s, s.begin():
        await s.execute(update(Device).values(last_synced_at=None))
    stats = await dev_svc.reconcile(ctx)
    assert stats["fixed_expiry"] == 1
    assert node.clients[d.client_name]["expires_at"] == sub.expires_at


async def test_suspend_and_resume_bans_on_node(ctx):
    await grant(ctx, 11)
    d = (await dev_svc.add_device(ctx, 11, node_code="fi")).device
    node = ctx.extra["nodes"]["fi"]
    ok, failed = await admin_svc.suspend(ctx, ADMIN_ID, "web", 11, "нарушение правил", suspended=True)
    assert (ok, failed) == (1, 0) and node.clients[d.client_name]["banned"]
    ok, failed = await admin_svc.suspend(ctx, ADMIN_ID, "web", 11, "разблокировка", suspended=False)
    assert ok == 1 and not node.clients[d.client_name]["banned"]
    async with ctx.session() as s:
        assert (await s.get(Device, d.id)).status == "active"


async def test_replace_device(ctx):
    await grant(ctx, 12)
    d = (await dev_svc.add_device(ctx, 12, node_code="fi", label="Ноутбук")).device
    res = await dev_svc.replace_device(ctx, 12, d.id, node_code="de")
    assert res.confirmed and res.device.node.code == "de" and res.device.label == "Ноутбук"
    assert d.client_name not in ctx.extra["nodes"]["fi"].clients


async def test_non_retryable_create_frees_slot(ctx):
    await grant(ctx, 13)
    ctx.extra["nodes"]["fi"].fail["create"] = VpnError("bad_name", code="bad_name", status=400)
    res = await dev_svc.add_device(ctx, 13, node_code="fi")
    assert not res.confirmed and res.device.status == "failed"
    assert (await dev_svc.add_device(ctx, 13, node_code="de")).confirmed

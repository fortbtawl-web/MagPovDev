from datetime import timedelta

from sqlalchemy import func, select

from app.models import Device
from app.services import devices as dev
from app.services.devices import DeviceError, sync_user_vpn
from app.services.users import extend_subscription
from app.vpn.mock import NotConfiguredVPNService
from tests.conftest import NOW, make_user, trial


async def paid_user(session, devices=2, tg=42):
    user = await make_user(session, tg_id=tg)
    await extend_subscription(session, user, 30, NOW, plan_code="duo", plan_title="Duo", device_limit=devices)
    await session.commit()
    return user


async def test_device_limit_enforced(session, vpn):
    user = await paid_user(session, devices=2)
    assert (await dev.add_device(session, vpn, user, "iPhone", NOW, "sp")).ok
    assert (await dev.add_device(session, vpn, user, "Mac", NOW, "sp")).ok
    res = await dev.add_device(session, vpn, user, "Third", NOW, "sp")
    assert res.error == DeviceError.LIMIT
    assert len(vpn.clients) == 2


async def test_each_device_gets_own_client(session, vpn):
    user = await paid_user(session)
    a = (await dev.add_device(session, vpn, user, "A", NOW, "sp")).device
    b = (await dev.add_device(session, vpn, user, "B", NOW, "sp")).device
    assert a.external_id != b.external_id and a.external_username != b.external_username
    la = (await dev.device_connection(session, vpn, user, a.public_id)).connection.link
    lb = (await dev.device_connection(session, vpn, user, b.public_id)).connection.link
    assert la != lb


async def test_no_device_without_subscription_or_infrastructure(session, vpn):
    user = await make_user(session)
    assert (await dev.add_device(session, vpn, user, "A", NOW, "sp")).error == DeviceError.NO_SUBSCRIPTION
    paid = await paid_user(session, tg=43)
    assert (await dev.add_device(session, NotConfiguredVPNService(), paid, "A", NOW, "sp")).error == DeviceError.NOT_READY


async def test_panel_failure_creates_nothing(session, vpn):
    user = await paid_user(session)
    vpn.fail_next = 5
    res = await dev.add_device(session, vpn, user, "A", NOW, "sp")
    assert res.error == DeviceError.VPN_UNAVAILABLE and res.device is None
    assert await session.scalar(select(func.count(Device.id))) == 0


async def test_revoke_removes_client_on_panel_first(session, vpn):
    user = await paid_user(session)
    d = (await dev.add_device(session, vpn, user, "A", NOW, "sp")).device
    vpn.fail_next = 1
    res = await dev.revoke_device(session, vpn, user, d.public_id, NOW)
    assert res.error == DeviceError.VPN_UNAVAILABLE and d.status == "active" and len(vpn.clients) == 1
    res = await dev.revoke_device(session, vpn, user, d.public_id, NOW)
    assert res.ok and d.status == "revoked" and vpn.clients == {}
    assert (await dev.add_device(session, vpn, user, "B", NOW, "sp")).ok  # slot freed


async def test_replace_issues_new_credentials(session, vpn):
    user = await paid_user(session, devices=1)
    d = (await dev.add_device(session, vpn, user, "Phone", NOW, "sp")).device
    old = d.external_id
    res = await dev.replace_device(session, vpn, user, d.public_id, None, NOW, "sp")
    assert res.ok and res.device.name == "Phone" and res.device.external_id != old
    assert list(vpn.clients) == [res.device.external_id]


async def test_rename_validation_and_ownership(session, vpn):
    user = await paid_user(session)
    other = await paid_user(session, tg=43)
    d = (await dev.add_device(session, vpn, user, "A", NOW, "sp")).device
    assert (await dev.rename_device(session, user, d.public_id, "  ")).error == DeviceError.INVALID_NAME
    assert (await dev.rename_device(session, user, d.public_id, "<b>Мой ПК</b>")).device.name == "bМой ПК/b"
    assert (await dev.rename_device(session, other, d.public_id, "stolen")).error == DeviceError.NOT_FOUND
    assert (await dev.device_connection(session, vpn, other, d.public_id)).error == DeviceError.NOT_FOUND


async def test_devices_over_limit_are_suspended_after_downgrade(session, vpn):
    user = await paid_user(session, devices=2)
    a = (await dev.add_device(session, vpn, user, "A", NOW, "sp")).device
    b = (await dev.add_device(session, vpn, user, "B", NOW, "sp")).device
    later = NOW + timedelta(days=40)  # expired, then a 1-device plan bought
    sub = await extend_subscription(session, user, 30, later, plan_code="solo", plan_title="Solo", device_limit=1)
    await session.commit()
    assert sub.device_limit == 1
    assert await sync_user_vpn(session, vpn, user, sub, later)
    assert a.status == "active" and b.status == "suspended"
    assert vpn.clients[a.external_id].enabled and not vpn.clients[b.external_id].enabled


async def test_trial_device_counts_toward_limit(session, vpn):
    user = await make_user(session)
    out = await trial(session, user, vpn)
    assert out.device is not None
    assert (await dev.add_device(session, vpn, user, "Second", NOW, "sp")).error == DeviceError.LIMIT

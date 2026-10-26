"""Device management: each device is a separate VPN client on the panel."""
from __future__ import annotations

import enum
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceStatus, Subscription, User, VPNErrorLog
from app.services.users import count_live_devices, get_subscription, is_active, live_devices
from app.vpn.base import VPNClient, VPNConnection, VPNError, VPNNotConfiguredError, VPNService, VPNUnavailableError

log = logging.getLogger("spirittvpn.devices")
NAME_RE = re.compile(r"[\x00-\x1f\x7f<>]")


class DeviceError(enum.Enum):
    NOT_READY = "VPN-серверы готовятся к запуску — устройства станут доступны после настройки инфраструктуры."
    NO_SUBSCRIPTION = "Нет активной подписки. Оформи тариф или активируй пробный период."
    LIMIT = "Все места для устройств заняты. Отзови одно из устройств или выбери тариф с большим лимитом."
    VPN_UNAVAILABLE = "VPN-сервер временно недоступен. Доступ не создан — попробуй через несколько минут."
    NOT_FOUND = "Устройство не найдено."
    INVALID_NAME = "Название: от 1 до 32 символов."
    NOT_ACTIVE = "Устройство сейчас неактивно — продли подписку, чтобы получить настройки."


@dataclass
class DeviceResult:
    device: Device | None = None
    error: DeviceError | None = None
    connection: VPNConnection | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def clean_name(raw: str | None) -> str | None:
    name = " ".join(NAME_RE.sub("", raw or "").split())
    return name if 1 <= len(name) <= 32 else None


def make_username(prefix: str, telegram_id: int) -> str:
    return f"{prefix}_{telegram_id}_{secrets.token_hex(3)}"  # ≤36 chars, unique per device


async def log_vpn_error(session: AsyncSession, operation: str, exc: Exception, device_id: int | None = None) -> None:
    session.add(VPNErrorLog(operation=operation, error=f"{type(exc).__name__}: {str(exc)[:200]}", device_id=device_id))
    await session.commit()


async def create_with_recovery(vpn: VPNService, username: str, telegram_id: int, expires_at: datetime) -> VPNClient:
    """POST is not retried blindly: after a transient failure we look the client up by its unique username."""
    try:
        return await vpn.create_client(username, telegram_id, expires_at)
    except VPNUnavailableError:
        existing = await vpn.find_client(username)
        if existing is None:
            raise
        log.info("recovered client after lost create response (tg=%s)", telegram_id)
        if not existing.enabled or existing.expires_at != expires_at:
            existing = await vpn.update_client(existing.external_id, expires_at=expires_at, enabled=True)
        return existing


async def provision_device(
    session: AsyncSession, vpn: VPNService, user: User, sub: Subscription, name: str, prefix: str
) -> Device:
    """Creates the panel client and the device row inside the caller's transaction. Raises VPNError."""
    device = Device(
        public_id=str(uuid.uuid4()), user_id=user.id, subscription_id=sub.id, name=name,
        status=DeviceStatus.PENDING, provider=vpn.name, external_username=make_username(prefix, user.telegram_id),
    )
    session.add(device)
    await session.flush()
    client = await create_with_recovery(vpn, device.external_username, user.telegram_id, sub.expires_at)
    device.external_id = client.external_id
    device.status = DeviceStatus.ACTIVE
    return device


async def _compensate(vpn: VPNService, device: Device) -> None:
    if device.external_id:
        try:
            await vpn.revoke_client(device.external_id)
        except VPNError:
            log.error("orphan VPN client left on panel: %s", device.external_username)


async def add_device(session: AsyncSession, vpn: VPNService, user: User, raw_name: str, now: datetime, prefix: str) -> DeviceResult:
    name = clean_name(raw_name)
    if name is None:
        return DeviceResult(error=DeviceError.INVALID_NAME)
    if not vpn.is_configured:
        return DeviceResult(error=DeviceError.NOT_READY)
    sub = await get_subscription(session, user.id, for_update=True)
    if not is_active(sub, now):
        return DeviceResult(error=DeviceError.NO_SUBSCRIPTION)
    assert sub is not None
    if await count_live_devices(session, user.id) >= sub.device_limit:
        return DeviceResult(error=DeviceError.LIMIT)
    try:
        device = await provision_device(session, vpn, user, sub, name, prefix)
    except VPNError as exc:
        await session.rollback()
        await log_vpn_error(session, "create_client", exc)
        return DeviceResult(error=DeviceError.VPN_UNAVAILABLE)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await _compensate(vpn, device)
        raise
    log.info("device added user_id=%s device=%s", user.id, device.public_id)
    return DeviceResult(device=device)


async def get_owned_device(session: AsyncSession, user_id: int, public_id: str) -> Device | None:
    return await session.scalar(
        select(Device).where(Device.public_id == public_id, Device.user_id == user_id, Device.status.in_(DeviceStatus.LIVE))
    )


async def rename_device(session: AsyncSession, user: User, public_id: str, raw_name: str) -> DeviceResult:
    name = clean_name(raw_name)
    if name is None:
        return DeviceResult(error=DeviceError.INVALID_NAME)
    device = await get_owned_device(session, user.id, public_id)
    if device is None:
        return DeviceResult(error=DeviceError.NOT_FOUND)
    device.name = name
    await session.commit()
    return DeviceResult(device=device)


async def revoke_device_row(session: AsyncSession, vpn: VPNService, device: Device, now: datetime) -> DeviceError | None:
    """Revokes on the panel first; the row is marked revoked only after the panel confirmed."""
    if device.external_id:
        if not vpn.is_configured:
            return DeviceError.NOT_READY
        try:
            await vpn.revoke_client(device.external_id)
        except VPNError as exc:
            device.last_error = type(exc).__name__
            await session.commit()
            await log_vpn_error(session, "revoke_client", exc, device.id)
            return DeviceError.VPN_UNAVAILABLE
    device.status = DeviceStatus.REVOKED
    device.revoked_at = now
    await session.commit()
    log.info("device revoked id=%s", device.public_id)
    return None


async def revoke_device(session: AsyncSession, vpn: VPNService, user: User, public_id: str, now: datetime) -> DeviceResult:
    device = await get_owned_device(session, user.id, public_id)
    if device is None:
        return DeviceResult(error=DeviceError.NOT_FOUND)
    err = await revoke_device_row(session, vpn, device, now)
    return DeviceResult(device=device, error=err)


async def replace_device(
    session: AsyncSession, vpn: VPNService, user: User, public_id: str, raw_name: str | None, now: datetime, prefix: str
) -> DeviceResult:
    device = await get_owned_device(session, user.id, public_id)
    if device is None:
        return DeviceResult(error=DeviceError.NOT_FOUND)
    name = raw_name or device.name
    if clean_name(name) is None:
        return DeviceResult(error=DeviceError.INVALID_NAME)
    err = await revoke_device_row(session, vpn, device, now)
    if err:
        return DeviceResult(error=err)
    return await add_device(session, vpn, user, name, now, prefix)


async def device_connection(session: AsyncSession, vpn: VPNService, user: User, public_id: str) -> DeviceResult:
    device = await get_owned_device(session, user.id, public_id)
    if device is None:
        return DeviceResult(error=DeviceError.NOT_FOUND)
    if device.status != DeviceStatus.ACTIVE or not device.external_id:
        return DeviceResult(device=device, error=DeviceError.NOT_ACTIVE)
    if not vpn.is_configured:
        return DeviceResult(device=device, error=DeviceError.NOT_READY)
    try:
        conn = await vpn.get_connection(device.external_id)
    except VPNError as exc:
        await log_vpn_error(session, "get_connection", exc, device.id)
        return DeviceResult(device=device, error=DeviceError.VPN_UNAVAILABLE)
    return DeviceResult(device=device, connection=conn)


async def sync_user_vpn(
    session: AsyncSession, vpn: VPNService, user: User, sub: Subscription, now: datetime, attempts: int = 3
) -> bool:
    """Pushes expiry/enabled state of the subscription to every live device. Devices beyond the limit are suspended.

    Commits. On failure the subscription keeps vpn_sync_needed and the background job retries.
    """
    if not vpn.is_configured:
        return False
    active = is_active(sub, now)
    ok = True
    for idx, device in enumerate(await live_devices(session, user.id)):
        if not device.external_id:
            continue
        want = active and idx < sub.device_limit
        try:
            await vpn.update_client(device.external_id, expires_at=sub.expires_at, enabled=want)
        except VPNNotConfiguredError:
            return False
        except VPNError as exc:
            ok = False
            device.last_error = type(exc).__name__
            session.add(VPNErrorLog(operation="update_client", error=f"{type(exc).__name__}: {str(exc)[:200]}",
                                    device_id=device.id))
            continue
        device.status = DeviceStatus.ACTIVE if want else DeviceStatus.SUSPENDED
        device.last_error = None
    if ok:
        sub.vpn_sync_needed = False
        sub.vpn_sync_attempts = 0
        sub.vpn_last_error = None
    else:
        sub.vpn_sync_attempts += 1
        sub.vpn_last_error = "device sync failed"
        log.warning("vpn sync failed for user_id=%s", user.id)
    await session.commit()
    return ok

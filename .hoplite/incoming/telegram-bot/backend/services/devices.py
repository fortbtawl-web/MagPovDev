"""Devices = individual VPN clients on H1//VLESS nodes. Every state change is confirmed by the node first."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.vpn import VpnClientInfo, VpnError, VpnHealth
from backend.models import Device, DevStatus, Subscription, SubStatus, VpnNode, VpnOperation
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow

log = logging.getLogger("spiritt.devices")
MAX_ATTEMPTS = 20


@dataclass
class OpResult:
    device: Device
    confirmed: bool
    error: str | None = None


def new_client_name(user_id: int) -> str:
    return f"sv{user_id}x{secrets.token_hex(4)}"


def clean_label(label: str | None, fallback: str) -> str:
    text = " ".join((label or "").split())[:48]
    return text or fallback


async def desired_node_expiry(session: AsyncSession, sub: Subscription) -> datetime:
    if sub.is_lifetime:
        horizon = int(await settings_svc.get(session, "lifetime_node_horizon_days"))
        return utcnow() + timedelta(days=horizon)
    if sub.expires_at is None:
        raise ServiceError("У подписки нет даты окончания.", "no_expiry")
    return sub.expires_at


def _backoff(attempts: int) -> datetime:
    return utcnow() + timedelta(seconds=min(60 * (2 ** min(attempts, 6)), 3600))


async def record_op(
    session: AsyncSession,
    *,
    device: Device | None,
    node_id: int | None,
    op_type: str,
    status: str,
    error: str | None = None,
    params: dict | None = None,
    actor: str = "system",
) -> VpnOperation:
    """Journal an operation. A pending row for (device, op_type) is updated instead of duplicated."""
    op = None
    if device is not None:
        op = await session.scalar(
            select(VpnOperation)
            .where(VpnOperation.device_id == device.id, VpnOperation.op_type == op_type, VpnOperation.status == "pending")
            .with_for_update()
        )
    if op is None:
        op = VpnOperation(device_id=device.id if device else None, node_id=node_id, op_type=op_type, params=params or {}, actor=actor, attempts=0)
        session.add(op)
    op.attempts = (op.attempts or 0) + 1
    if status == "pending" and op.attempts >= MAX_ATTEMPTS:
        status = "failed"
    op.status = status
    op.last_error = error
    op.next_retry_at = _backoff(op.attempts) if status == "pending" else None
    await session.flush()
    return op


def _apply_info(device: Device, info: VpnClientInfo) -> None:
    device.client_uuid = info.uuid or device.client_uuid
    device.subscription_url = info.subscription_url or device.subscription_url
    device.connection_link = info.link or device.connection_link
    device.node_expires_at = info.expires_at
    device.last_synced_at = utcnow()
    device.last_error = None


async def get_node(session: AsyncSession, code: str | None) -> VpnNode:
    if code:
        node = await session.scalar(select(VpnNode).where(VpnNode.code == code, VpnNode.is_active.is_(True)))
        if node is None:
            raise ServiceError("Сервер недоступен для подключения.", "bad_node")
        if node.last_health_ok is False:
            # Don't park a pending client on a node that failed its last check; the user can pick another.
            raise ServiceError(
                f"Локация {node.flag} {node.name} временно недоступна. Выберите другую страну — "
                "мы уже проверяем сервер.",
                "node_down",
            )
        return node
    nodes = (await session.execute(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))).scalars().all()
    if not nodes:
        raise ServiceError("VPN-серверы ещё не подключены. Мы уже работаем над этим.", "no_nodes")
    # Prefer healthy nodes with the fewest of our clients.
    counts = dict(
        (
            await session.execute(
                select(Device.node_id, func.count(Device.id))
                .where(Device.status.in_(DevStatus.OCCUPYING))
                .group_by(Device.node_id)
            )
        ).all()
    )
    healthy = [n for n in nodes if n.last_health_ok is not False] or nodes
    all_locations = [n for n in healthy if n.country_code == "ZZ" and n.last_health_ok is not False]
    if all_locations:  # one subscription with every location is the default offer
        return all_locations[0]
    return min(healthy, key=lambda n: counts.get(n.id, 0))


async def list_devices(session: AsyncSession, user_id: int, *, include_revoked: bool = False) -> list[Device]:
    stmt = select(Device).where(Device.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(Device.status.notin_((DevStatus.REVOKED, DevStatus.FAILED)))
    return list((await session.execute(stmt.order_by(Device.created_at))).scalars())


async def occupied_slots(session: AsyncSession, sub_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(Device.id)).where(Device.subscription_id == sub_id, Device.status.in_(DevStatus.OCCUPYING))
        )
        or 0
    )


async def _owned_device(session: AsyncSession, device_id: int, user_id: int | None, *, lock: bool = True) -> Device:
    stmt = select(Device).where(Device.id == device_id)
    if lock:
        stmt = stmt.with_for_update(of=Device)
    device = await session.scalar(stmt)
    if device is None or (user_id is not None and device.user_id != user_id):
        raise ServiceError("Устройство не найдено.", "not_found")
    return device


async def reserve_device(
    session: AsyncSession, sub: Subscription, node: VpnNode, label: str, *, allow_pending_sub: bool = False
) -> Device:
    """Insert a pending device under the subscription row lock (caller holds it)."""
    if sub.status != SubStatus.ACTIVE and not (allow_pending_sub and sub.status == SubStatus.PENDING):
        raise ServiceError("Подписка не активна. Продлите её, чтобы добавить устройство.", "not_active")
    used = await occupied_slots(session, sub.id)
    if used >= sub.device_limit:
        raise ServiceError(
            f"Достигнут лимит тарифа: {used} из {sub.device_limit} устройств. Отключите одно из устройств или смените тариф.",
            "device_limit",
        )
    device = Device(
        user_id=sub.user_id,
        subscription_id=sub.id,
        node_id=node.id,
        client_name=new_client_name(sub.user_id),
        label=label,
        status=DevStatus.PENDING,
    )
    session.add(device)
    await session.flush()
    return device


async def add_device(ctx: AppContext, user_id: int, *, node_code: str | None = None, label: str | None = None, actor: str = "user") -> OpResult:
    async with ctx.session() as session, session.begin():
        from backend.models import User

        owner = await session.get(User, user_id)
        if owner is not None and owner.is_banned and not actor.startswith("admin:"):
            raise ServiceError("Аккаунт заблокирован. Обратитесь в поддержку.", "banned")
        sub = await subs_svc.get_current(session, user_id, lock=True)
        if sub is None:
            raise ServiceError("У вас нет активной подписки.", "no_subscription")
        node = await get_node(session, node_code)
        used = await occupied_slots(session, sub.id)
        device = await reserve_device(session, sub, node, clean_label(label, f"Устройство {used + 1}"))
        device_id = device.id
    return await provision_device(ctx, device_id, actor=actor)


async def provision_device(ctx: AppContext, device_id: int, *, expires_at: datetime | None = None, actor: str = "system") -> OpResult:
    """Create the client on the node (idempotent by client_name) and mark the device active on confirmation."""
    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, None)
        if device.status != DevStatus.PENDING:
            return OpResult(device, device.status == DevStatus.ACTIVE)
        sub = await session.get(Subscription, device.subscription_id)
        assert sub is not None
        target = expires_at or await desired_node_expiry(session, sub)
        node_code = device.node.code
        name = device.client_name
        per_key_limit = int(await settings_svc.get(session, "node_client_device_limit"))

    try:
        info = await ctx.vpn.get(node_code).create_client(name, expires_at=target, device_limit=per_key_limit)
    except VpnError as exc:
        async with ctx.session() as session, session.begin():
            device = await _owned_device(session, device_id, None)
            device.last_error = str(exc)
            if exc.retryable:
                op = await record_op(session, device=device, node_id=device.node_id, op_type="create", status="pending", error=str(exc), actor=actor)
                if op.status == "failed":  # retry budget exhausted
                    device.status = DevStatus.FAILED
            else:
                device.status = DevStatus.FAILED
                await record_op(session, device=device, node_id=device.node_id, op_type="create", status="failed", error=str(exc), actor=actor)
        log.warning("provision device %s failed: %s", device_id, exc.code)
        return OpResult(device, False, str(exc))

    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, None)
        _apply_info(device, info)
        if device.status == DevStatus.PENDING:
            device.status = DevStatus.ACTIVE
            device.activated_at = utcnow()
        await record_op(session, device=device, node_id=device.node_id, op_type="create", status="success", actor=actor)
    return OpResult(device, True)


async def revoke_device(ctx: AppContext, device_id: int, *, user_id: int | None, actor: str = "user") -> OpResult:
    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, user_id)
        if device.status == DevStatus.REVOKED:
            return OpResult(device, True)
        if device.status != DevStatus.FAILED:
            device.status = DevStatus.REVOKING
        await record_op(session, device=device, node_id=device.node_id, op_type="revoke", status="pending", actor=actor)
        node_code, name = device.node.code, device.client_name
    return await _do_revoke(ctx, device_id, node_code, name, actor)


async def _do_revoke(ctx: AppContext, device_id: int, node_code: str, name: str, actor: str) -> OpResult:
    try:
        await ctx.vpn.get(node_code).revoke_client(name)
    except VpnError as exc:
        async with ctx.session() as session, session.begin():
            device = await _owned_device(session, device_id, None)
            device.last_error = str(exc)
            # The device stays "revoking" (and keeps its slot) until the node confirms.
            await record_op(session, device=device, node_id=device.node_id, op_type="revoke", status="pending", error=str(exc), actor=actor)
        return OpResult(device, False, str(exc))
    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, None)
        if device.status != DevStatus.FAILED:
            device.status = DevStatus.REVOKED
        device.revoked_at = utcnow()
        device.subscription_url = None
        device.connection_link = None
        device.last_error = None
        await record_op(session, device=device, node_id=device.node_id, op_type="revoke", status="success", actor=actor)
        # A pending create for a revoked device must not run any more.
        for op in (await session.execute(select(VpnOperation).where(VpnOperation.device_id == device.id, VpnOperation.status == "pending"))).scalars():
            op.status = "failed"
            op.last_error = "superseded by revoke"
    return OpResult(device, True)


async def replace_device(ctx: AppContext, user_id: int, device_id: int, *, node_code: str | None = None) -> OpResult:
    async with ctx.session() as session:
        old = await _owned_device(session, device_id, user_id, lock=False)
        if old.status not in (DevStatus.ACTIVE, DevStatus.EXPIRED, DevStatus.SUSPENDED):
            raise ServiceError("Это устройство нельзя заменить в текущем состоянии.", "bad_state")
        limit = int(await settings_svc.get(session, "max_device_replacements_per_day"))
        recent = await session.scalar(
            select(func.count(Device.id)).where(
                Device.user_id == user_id, Device.status == DevStatus.REVOKED, Device.revoked_at > utcnow() - timedelta(days=1)
            )
        )
        if int(recent or 0) >= limit:
            raise ServiceError("Слишком много замен за сутки. Попробуйте позже или напишите в поддержку.", "replace_limit")
        label, code = old.label, node_code or old.node.code
        await get_node(session, code)  # fail before revoking if the target location is unusable
    revoked = await revoke_device(ctx, device_id, user_id=user_id)
    if not revoked.confirmed:
        raise ServiceError("Сервер не подтвердил отключение старого устройства. Замена отменена, попробуйте позже.", "revoke_failed")
    return await add_device(ctx, user_id, node_code=code, label=label)


async def rename_device(ctx: AppContext, user_id: int, device_id: int, label: str) -> Device:
    label = clean_label(label, "")
    if not label:
        raise ServiceError("Название не может быть пустым.", "bad_label")
    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, user_id)
        if device.status in (DevStatus.REVOKED, DevStatus.FAILED):
            raise ServiceError("Устройство уже отключено.", "bad_state")
        device.label = label
    return device


async def refresh_link(ctx: AppContext, user_id: int | None, device_id: int) -> tuple[Device, bool]:
    """Fetch the real link from the node. Returns (device, live) — live=False means cached data."""
    async with ctx.session() as session:
        device = await _owned_device(session, device_id, user_id, lock=False)
        if device.status not in (DevStatus.ACTIVE, DevStatus.EXPIRED, DevStatus.SUSPENDED):
            raise ServiceError("Ссылка доступна только для активного устройства.", "bad_state")
        node_code, name = device.node.code, device.client_name
    try:
        info = await ctx.vpn.get(node_code).get_client(name)
    except VpnError as exc:
        log.info("link refresh failed for device %s: %s", device_id, exc.code)
        return device, False
    async with ctx.session() as session, session.begin():
        device = await _owned_device(session, device_id, None)
        if info is None:
            device.last_error = "Клиент не найден на сервере"
            await record_op(session, device=device, node_id=device.node_id, op_type="sync", status="failed", error="missing_on_node")
            return device, False
        _apply_info(device, info)
    return device, True


async def sync_subscription(ctx: AppContext, sub_id: int, *, actor: str = "system") -> tuple[int, int]:
    """Push the subscription's expiry to every live device. Returns (ok, failed)."""
    async with ctx.session() as session:
        sub = await session.get(Subscription, sub_id)
        if sub is None:
            return 0, 0
        target = await desired_node_expiry(session, sub)
        devices = (
            await session.execute(
                select(Device.id, Device.client_name, VpnNode.code)
                .join(VpnNode, VpnNode.id == Device.node_id)
                .where(Device.subscription_id == sub_id, Device.status.in_((DevStatus.ACTIVE, DevStatus.EXPIRED, DevStatus.SUSPENDED)))
            )
        ).all()
        sub_status = sub.status
    ok = failed = 0
    for dev_id, name, code in devices:
        try:
            info = await ctx.vpn.get(code).extend_client(name, expires_at=target)
        except VpnError as exc:
            failed += 1
            async with ctx.session() as session, session.begin():
                device = await _owned_device(session, dev_id, None)
                device.last_error = str(exc)
                await record_op(session, device=device, node_id=device.node_id, op_type="set_expiry", status="pending", error=str(exc), actor=actor)
            continue
        ok += 1
        async with ctx.session() as session, session.begin():
            device = await _owned_device(session, dev_id, None)
            _apply_info(device, info)
            if device.status == DevStatus.EXPIRED and sub_status == SubStatus.ACTIVE:
                device.status = DevStatus.ACTIVE
            await record_op(session, device=device, node_id=device.node_id, op_type="set_expiry", status="success", actor=actor)
    return ok, failed


async def set_suspended(ctx: AppContext, sub_id: int, suspended: bool, *, actor: str) -> tuple[int, int]:
    """Ban/unban every live device on its node after the DB status changed. Returns (ok, failed)."""
    async with ctx.session() as session:
        rows = (
            await session.execute(
                select(Device.id, Device.client_name, VpnNode.code)
                .join(VpnNode, VpnNode.id == Device.node_id)
                .where(Device.subscription_id == sub_id, Device.status.in_((DevStatus.ACTIVE, DevStatus.EXPIRED, DevStatus.SUSPENDED)))
            )
        ).all()
    ok = failed = 0
    op_type = "ban" if suspended else "unban"
    for dev_id, name, code in rows:
        try:
            provider = ctx.vpn.get(code)
            if suspended:
                await provider.suspend_client(name, reason="suspended by SpirittVPN admin")
            else:
                await provider.resume_client(name)
        except VpnError as exc:
            failed += 1
            async with ctx.session() as session, session.begin():
                device = await _owned_device(session, dev_id, None)
                device.last_error = str(exc)
                await record_op(session, device=device, node_id=device.node_id, op_type=op_type, status="pending", error=str(exc), actor=actor)
            continue
        ok += 1
        async with ctx.session() as session, session.begin():
            device = await _owned_device(session, dev_id, None)
            sub = await session.get(Subscription, sub_id)
            if suspended:
                device.status = DevStatus.SUSPENDED
            else:
                device.status = DevStatus.ACTIVE if subs_svc.is_usable(sub) else DevStatus.EXPIRED
            device.last_error = None
            await record_op(session, device=device, node_id=device.node_id, op_type=op_type, status="success", actor=actor)
    if not suspended:
        await sync_subscription(ctx, sub_id, actor=actor)
    return ok, failed


async def retry_pending(ctx: AppContext, limit: int = 50) -> int:
    """Retry journaled operations whose outcome is unknown or that failed transiently."""
    async with ctx.session() as session:
        ops = (
            await session.execute(
                select(VpnOperation.id, VpnOperation.device_id, VpnOperation.op_type)
                .where(VpnOperation.status == "pending", VpnOperation.device_id.is_not(None), VpnOperation.next_retry_at <= utcnow())
                .order_by(VpnOperation.next_retry_at)
                .limit(limit)
            )
        ).all()
    done = 0
    for _op_id, device_id, op_type in ops:
        try:
            async with ctx.session() as session:
                device = await session.get(Device, device_id)
                if device is None:
                    continue
                status, node_code, name, sub_id = device.status, device.node.code, device.client_name, device.subscription_id
            if op_type == "create" and status == DevStatus.PENDING:
                await provision_device(ctx, device_id)
            elif op_type == "revoke":
                await _do_revoke(ctx, device_id, node_code, name, "system")
            elif op_type == "set_expiry":
                await sync_subscription(ctx, sub_id)
            elif op_type in ("ban", "unban"):
                async with ctx.session() as session:
                    sub = await session.get(Subscription, sub_id)
                    want_suspended = sub is not None and not subs_svc.is_usable(sub)
                await set_suspended(ctx, sub_id, want_suspended, actor="system")
            else:
                async with ctx.session() as session, session.begin():
                    dev = await session.get(Device, device_id)
                    await record_op(session, device=dev, node_id=dev.node_id if dev else None, op_type=op_type, status="failed", error="not retryable in current state")
            done += 1
        except Exception:  # noqa: BLE001 - keep the loop going; errors are journaled by the callee
            log.exception("retry of %s for device %s crashed", op_type, device_id)
    return done


async def reconcile(ctx: AppContext, limit: int = 100) -> dict[str, int]:
    """Compare DB devices with node state and repair drift."""
    stats = {"checked": 0, "recreated": 0, "fixed_expiry": 0, "unbanned": 0, "errors": 0}
    stale = utcnow() - timedelta(hours=6)
    async with ctx.session() as session:
        rows = (
            await session.execute(
                select(Device.id)
                .where(
                    Device.status.in_((DevStatus.ACTIVE, DevStatus.EXPIRED, DevStatus.SUSPENDED)),
                    (Device.last_synced_at.is_(None)) | (Device.last_synced_at < stale),
                )
                .order_by(Device.last_synced_at.nulls_first())
                .limit(limit)
            )
        ).scalars().all()
    for device_id in rows:
        stats["checked"] += 1
        async with ctx.session() as session:
            device = await session.get(Device, device_id)
            sub = await session.get(Subscription, device.subscription_id)
            target = await desired_node_expiry(session, sub) if (sub.is_lifetime or sub.expires_at) else None
            node_code, name, dstatus = device.node.code, device.client_name, device.status
            horizon_half = timedelta(days=int(await settings_svc.get(session, "lifetime_node_horizon_days")) / 2)
        try:
            info = await ctx.vpn.get(node_code).get_client(name)
        except VpnError as exc:
            stats["errors"] += 1
            async with ctx.session() as session, session.begin():
                dev = await session.get(Device, device_id)
                dev.last_error = str(exc)
                await record_op(session, device=dev, node_id=dev.node_id, op_type="sync", status="failed", error=str(exc))
            continue
        if info is None:
            if sub.status == SubStatus.ACTIVE and subs_svc.is_usable(sub):
                async with ctx.session() as session, session.begin():
                    dev = await session.get(Device, device_id, with_for_update=True)
                    dev.status = DevStatus.PENDING
                    dev.last_error = "Клиент отсутствовал на сервере — пересоздаём"
                    await record_op(session, device=dev, node_id=dev.node_id, op_type="sync", status="failed", error="missing_on_node")
                await provision_device(ctx, device_id)
                stats["recreated"] += 1
            continue
        need_expiry = False
        if target is not None and info.expires_at is not None:
            if sub.is_lifetime:
                need_expiry = info.expires_at < utcnow() + horizon_half
            else:
                need_expiry = abs((info.expires_at - target).total_seconds()) > 120
        if need_expiry:
            await sync_subscription(ctx, sub.id)
            stats["fixed_expiry"] += 1
        if info.banned and sub.status == SubStatus.ACTIVE and dstatus != DevStatus.SUSPENDED:
            await set_suspended(ctx, sub.id, False, actor="system")
            stats["unbanned"] += 1
        elif not info.banned and sub.status == SubStatus.SUSPENDED:
            await set_suspended(ctx, sub.id, True, actor="system")
        async with ctx.session() as session, session.begin():
            dev = await session.get(Device, device_id)
            _apply_info(dev, info)
    return stats


async def check_nodes(ctx: AppContext) -> list[dict]:
    async with ctx.session() as session:
        nodes = (await session.execute(select(VpnNode).order_by(VpnNode.id))).scalars().all()
        targets = [(n.id, n.code) for n in nodes]
    out = []
    for node_id, code in targets:
        try:
            health = await ctx.vpn.get(code).health_check()
        except VpnError as exc:
            health = VpnHealth(ok=False, detail=str(exc))
        async with ctx.session() as session, session.begin():
            node = await session.get(VpnNode, node_id)
            node.last_health_ok = health.ok
            node.last_health_at = utcnow()
            node.last_error = None if health.ok and health.detail == "ok" else health.detail[:500]
            if health.clients is not None:
                node.remote_clients = health.clients
        out.append({"code": code, "ok": health.ok, "detail": health.detail, "clients": health.clients})
    return out


async def ensure_nodes(ctx: AppContext) -> None:
    """Upsert vpn_nodes from VPN_NODES. Nodes removed from config are deactivated, never deleted."""
    specs = ctx.settings.node_specs()
    async with ctx.session() as session, session.begin():
        existing = {n.code: n for n in (await session.execute(select(VpnNode))).scalars()}
        for spec in specs:
            node = existing.get(spec.code)
            if node is None:
                session.add(VpnNode(code=spec.code, name=spec.name, country_code=spec.country_code, api_url=spec.api_url))
            else:
                node.name, node.country_code, node.api_url, node.is_active = spec.name, spec.country_code, spec.api_url, True
        configured = {s.code for s in specs}
        for code, node in existing.items():
            if code not in configured and node.is_active:
                node.is_active = False

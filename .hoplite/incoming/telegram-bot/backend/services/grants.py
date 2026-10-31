"""Manual subscription grants by admins (any user identified by Telegram ID / @username / SpirittVPN ID)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from backend.models import Device, DevStatus, Plan, Subscription, SubStatus, User
from backend.services import admin as admin_svc
from backend.services import devices as dev_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.services.durations import parse_datetime, shift
from backend.utils import fmt_dt, utcnow

MODES = ("extend", "replace", "set_end", "lifetime", "shorten", "terminate", "revoke")
MODE_LABELS = {
    "extend": "Продление",
    "replace": "Новая подписка",
    "set_end": "Изменение даты окончания",
    "lifetime": "Бессрочный доступ",
    "shorten": "Сокращение срока",
    "terminate": "Досрочное завершение",
    "revoke": "Отзыв доступа",
}


@dataclass
class GrantResult:
    subscription: Subscription | None
    vpn: dict = field(default_factory=lambda: {"synced": 0, "failed": 0, "revoked": 0, "provisioned": None, "pending": 0})
    message: str = ""


def _usable_after(sub: Subscription) -> bool:
    return sub.status == SubStatus.ACTIVE and (sub.is_lifetime or (sub.expires_at is not None and sub.expires_at > utcnow()))


async def grant(
    ctx: AppContext,
    admin_id: int,
    source: str,
    user_id: int,
    *,
    mode: str,
    amount: int | None = None,
    unit: str | None = None,
    plan_code: str | None = None,
    ends_at: str | datetime | None = None,
    starts_at: str | datetime | None = None,
    device_limit: int | None = None,
    reason: str,
    note: str | None = None,
    notify: bool = True,
) -> GrantResult:
    reason = admin_svc.require_reason(reason)
    note = (note or "").strip()[:200] or None
    if mode not in MODES:
        raise ServiceError("Неизвестный тип операции.", "bad_mode")
    if device_limit is not None and not 1 <= device_limit <= 50:
        raise ServiceError("Лимит устройств: от 1 до 50.", "bad_limit")
    tz = ctx.settings.display_timezone
    now = utcnow()

    async with ctx.session() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ServiceError("Пользователь не найден.", "not_found")
        if plan_code:
            plan = await session.scalar(select(Plan).where(Plan.code == plan_code))
            if plan is None:
                raise ServiceError("Тариф не найден.", "bad_plan")
            if mode not in ("extend", "replace"):
                raise ServiceError("Тариф можно указать только для продления или новой подписки.", "bad_mode")
            if plan.is_lifetime:
                mode = "lifetime"
            else:
                amount, unit = plan.duration_days, "days"
            if device_limit is None:
                device_limit = plan.device_limit
        if mode in ("extend", "shorten") or (mode == "replace" and ends_at is None):
            if not amount or amount <= 0 or amount > 100_000 or not unit:
                raise ServiceError("Укажите срок: количество и единицу (часы, дни, недели, месяцы).", "bad_duration")
        end_dt = parse_datetime(ends_at, tz) if ends_at else None
        start_dt = parse_datetime(starts_at, tz) if starts_at else None
        if start_dt and start_dt > now:
            raise ServiceError("Дата начала не может быть в будущем.", "bad_start")
        if mode == "set_end" and end_dt is None:
            raise ServiceError("Укажите дату окончания.", "bad_date")
        if end_dt is not None and end_dt <= now:
            raise ServiceError("Дата окончания должна быть в будущем. Для завершения используйте «Завершить досрочно».", "bad_date")

        sub = await subs_svc.get_current(session, user_id, lock=True)
        old = admin_svc._sub_dict(sub)
        old_status, old_expires = (sub.status, sub.expires_at) if sub else (None, None)
        if mode in ("shorten", "terminate", "revoke") and sub is None:
            raise ServiceError("У пользователя нет подписки.", "no_subscription")
        if sub is not None and sub.status == SubStatus.PENDING:
            raise ServiceError("Подписка сейчас оформляется (пробный период). Повторите через минуту.", "pending")

        created = False
        if sub is None:
            sub = Subscription(user_id=user_id, plan_code="admin", status=SubStatus.ACTIVE, device_limit=device_limit or 1, source="admin", starts_at=start_dt or now)
            created = True

        if mode == "extend":
            if sub.is_lifetime:
                raise ServiceError("Подписка уже бессрочная — продление не требуется.", "lifetime")
            active_left = sub.expires_at is not None and sub.expires_at > now and sub.status in (SubStatus.ACTIVE, SubStatus.SUSPENDED)
            base = sub.expires_at if active_left else now
            if not active_left and sub.status == SubStatus.EXPIRED:
                sub.status, sub.starts_at = SubStatus.ACTIVE, now
            sub.expires_at = shift(base, amount, unit, tz)
        elif mode == "replace":
            sub.is_lifetime = False
            sub.starts_at = start_dt or now
            sub.expires_at = end_dt or shift(now, amount, unit, tz)
            if sub.status == SubStatus.EXPIRED:
                sub.status = SubStatus.ACTIVE
        elif mode == "set_end":
            sub.is_lifetime = False
            sub.expires_at = end_dt
            if sub.status == SubStatus.EXPIRED:
                sub.status, sub.starts_at = SubStatus.ACTIVE, now
        elif mode == "lifetime":
            sub.is_lifetime, sub.expires_at = True, None
            if sub.status == SubStatus.EXPIRED:
                sub.status, sub.starts_at = SubStatus.ACTIVE, now
        elif mode == "shorten":
            if sub.is_lifetime or sub.expires_at is None:
                raise ServiceError("У бессрочной подписки нет срока — используйте «Точная дата окончания» или «Завершить досрочно».", "lifetime")
            sub.expires_at = shift(sub.expires_at, -amount, unit, tz)
            if sub.expires_at <= now and sub.status == SubStatus.ACTIVE:
                sub.status = SubStatus.EXPIRED
        elif mode == "terminate":
            sub.is_lifetime = False
            sub.expires_at = now
            if sub.status in (SubStatus.ACTIVE, SubStatus.SUSPENDED):
                sub.status = SubStatus.EXPIRED
        elif mode == "revoke":
            if sub.is_lifetime:
                sub.is_lifetime = False
            sub.expires_at = sub.expires_at if sub.expires_at and sub.expires_at <= now else now
            sub.status = SubStatus.REVOKED

        if device_limit is not None and mode not in ("terminate", "revoke"):
            if not created:
                used = await dev_svc.occupied_slots(session, sub.id)
                if used > device_limit:
                    raise ServiceError(f"Сейчас подключено {used} устройств — больше нового лимита {device_limit}. Сначала отключите лишние.", "too_many_devices")
            sub.device_limit = device_limit
        if created:
            session.add(sub)
        await session.flush()
        subs_svc.log_event(
            session, sub, f"admin_{mode}", actor=f"admin:{admin_id}", old_status=old_status, old_expires=old_expires,
            days=amount if unit == "days" else None, comment=reason,
        ).public_note = note
        admin_svc.audit(
            session, admin_id=admin_id, source=source, action=f"subscription.{mode}", entity_type="subscription", entity_id=sub.id,
            reason=reason, old=old, new={**(admin_svc._sub_dict(sub) or {}), "amount": amount, "unit": unit, "note": note, "user_id": user_id},
        )
        sub_id, usable = sub.id, _usable_after(sub)
        sub_status = sub.status
        auto = bool(await settings_svc.get(session, "auto_provision_first_device"))
        devices = list((await session.execute(select(Device).where(Device.subscription_id == sub_id, Device.status.in_(DevStatus.OCCUPYING)))).scalars())

    result = GrantResult(subscription=None)
    vpn = result.vpn
    actor = f"admin:{admin_id}"
    if mode == "revoke":
        for d in devices:
            res = await dev_svc.revoke_device(ctx, d.id, user_id=None, actor=actor)
            vpn["revoked" if res.confirmed else "failed"] += 1
    elif mode == "terminate" or (mode == "shorten" and not usable and sub_status == SubStatus.EXPIRED):
        async with ctx.session() as session, session.begin():
            await session.execute(
                Device.__table__.update().where(Device.subscription_id == sub_id, Device.status == DevStatus.ACTIVE).values(status=DevStatus.EXPIRED)
            )
        # Node expiry must be in the future, so access is cut by banning the keys.
        ok, failed = await dev_svc.set_suspended(ctx, sub_id, True, actor=actor)
        vpn["synced"], vpn["failed"] = ok, failed
    elif usable:
        if not devices and auto:
            try:
                res = await dev_svc.add_device(ctx, user_id, label="Устройство 1", actor=actor)
                vpn["provisioned"] = res.confirmed
                if not res.confirmed:
                    vpn["failed"] += 1
            except ServiceError as exc:
                vpn["provisioned"] = False
                result.message = f"Подписка выдана, но устройство не создано: {exc.message.rstrip('.')}. "
        elif any(d.status in (DevStatus.EXPIRED, DevStatus.SUSPENDED) for d in devices):
            ok, failed = await dev_svc.set_suspended(ctx, sub_id, False, actor=actor)
            vpn["synced"], vpn["failed"] = ok, failed
        else:
            ok, failed = await dev_svc.sync_subscription(ctx, sub_id, actor=actor)
            vpn["synced"], vpn["failed"] = ok, failed
    elif devices:
        ok, failed = await dev_svc.sync_subscription(ctx, sub_id, actor=actor)
        vpn["synced"], vpn["failed"] = ok, failed

    async with ctx.session() as session:
        sub = await session.get(Subscription, sub_id)
        from sqlalchemy import func

        vpn["pending"] = int(
            await session.scalar(
                select(func.count(Device.id)).where(Device.subscription_id == sub_id, Device.status.in_((DevStatus.PENDING, DevStatus.REVOKING)))
            )
            or 0
        )
    result.subscription = sub
    until = "бессрочно" if sub.is_lifetime else (f"до {fmt_dt(sub.expires_at)}" if sub.expires_at else "")
    result.message += f"{MODE_LABELS[mode]}: готово. Статус: {sub.status} {until}".strip()
    if vpn["failed"] or vpn["pending"]:
        result.message += ". ⚠️ VPN-сервер подтвердил не всё — операции будут повторены автоматически."

    if notify:
        text = {
            "revoke": "⛔ Доступ к SpirittVPN отозван администратором.",
            "terminate": "⏹ Ваша подписка SpirittVPN завершена администратором.",
            "shorten": f"ℹ️ Срок подписки изменён администратором: {until}.",
        }.get(mode, f"🎁 Администратор обновил вашу подписку SpirittVPN: {'♾ бессрочно' if sub.is_lifetime else until}.")
        if note:
            text += f"\n\n💬 {note}"
        if mode not in ("revoke", "terminate") and (vpn["failed"] or vpn["pending"]):
            text += "\n\n⏳ VPN-сервер ещё не подтвердил подключение — мы повторим автоматически."
        await ctx.notifier.send(user_id, text)
    return result

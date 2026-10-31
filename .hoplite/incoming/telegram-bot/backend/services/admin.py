"""Administrative operations shared by the Telegram admin menu and the web panel. Everything is audited."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    AuditLog,
    BonusTransaction,
    Device,
    DevStatus,
    Order,
    OrderStatus,
    Plan,
    Referral,
    ReferralReward,
    Subscription,
    SubStatus,
    TrialActivation,
    User,
    VpnNode,
    VpnOperation,
)
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow


def require_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ServiceError("Укажите причину изменения (не менее 3 символов).", "reason_required")
    return reason[:500]


async def find_users(session: AsyncSession, q: str, limit: int = 50) -> list[User]:
    """Search by Telegram ID, @username (or part of it), SpirittVPN ID (SV-XXXXXX)."""
    from sqlalchemy import or_

    q = (q or "").strip()
    stmt = select(User).order_by(User.created_at.desc()).limit(limit)
    if not q:
        return list((await session.execute(stmt)).scalars())
    public = ref_svc.normalize_public_id(q)
    conds = []
    if public:
        conds.append(User.public_id == public)
    if q.lstrip("-").isdigit() and len(q) <= 16:
        conds.append(User.id == int(q))
    name = q.lstrip("@")
    if name and not name.isdigit():
        # LIKE wildcards in user input are escaped.
        safe = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append(User.username.ilike(f"%{safe}%", escape="\\"))
    if not conds:
        return []
    return list((await session.execute(stmt.where(or_(*conds)))).scalars())


async def resolve_user(session: AsyncSession, q: str) -> User:
    """Exactly one user by Telegram ID, exact @username or SpirittVPN ID."""
    q = (q or "").strip()
    public = ref_svc.normalize_public_id(q)
    user = None
    if public:
        user = await session.scalar(select(User).where(User.public_id == public))
    if user is None and q.isdigit():
        user = await session.get(User, int(q))
    if user is None and q.lstrip("@"):
        user = await session.scalar(select(User).where(func.lower(User.username) == q.lstrip("@").lower()))
    if user is None:
        raise ServiceError("Пользователь не найден. Он должен хотя бы раз открыть бота.", "not_found")
    return user


async def set_blocked(ctx: AppContext, admin_id: int, source: str, user_id: int, reason: str, *, blocked: bool) -> tuple[User, int, int]:
    from backend.services import roles

    reason = require_reason(reason)
    if blocked and await roles.is_staff(ctx, user_id):
        raise ServiceError("Нельзя заблокировать администратора. Сначала снимите права.", "is_admin")
    sync: bool | None = None
    async with ctx.session() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ServiceError("Пользователь не найден.", "not_found")
        if user.is_banned == blocked:
            raise ServiceError("Пользователь уже заблокирован." if blocked else "Пользователь не заблокирован.", "already")
        user.is_banned = blocked
        sub = await subs_svc.get_current(session, user_id, lock=True)
        if sub is not None:
            if blocked and sub.status != SubStatus.SUSPENDED:
                old_status = sub.status
                sub.status, sub.suspended_reason = SubStatus.SUSPENDED, f"blocked: {reason}"
                subs_svc.log_event(session, sub, "blocked", actor=f"admin:{admin_id}", old_status=old_status, comment=reason)
                sync = True
            elif not blocked and sub.status == SubStatus.SUSPENDED and (sub.suspended_reason or "").startswith("blocked:"):
                sub.status = SubStatus.ACTIVE if (sub.is_lifetime or (sub.expires_at and sub.expires_at > utcnow())) else SubStatus.EXPIRED
                sub.suspended_reason = None
                subs_svc.log_event(session, sub, "unblocked", actor=f"admin:{admin_id}", old_status=SubStatus.SUSPENDED, comment=reason)
                sync = False
        audit(session, admin_id=admin_id, source=source, action="user.block" if blocked else "user.unblock", entity_type="user", entity_id=user_id, reason=reason, old={"is_banned": not blocked}, new={"is_banned": blocked})
        sub_id = sub.id if sub else None
    ok = failed = 0
    if sync is not None and sub_id:
        ok, failed = await dev_svc.set_suspended(ctx, sub_id, sync, actor=f"admin:{admin_id}")
    await ctx.notifier.send(user_id, "🚫 Ваш аккаунт SpirittVPN заблокирован. Вопросы — в поддержку: /support" if blocked else "✅ Ваш аккаунт SpirittVPN разблокирован.")
    return user, ok, failed


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def audit(
    session: AsyncSession,
    *,
    admin_id: int,
    source: str,
    action: str,
    entity_type: str,
    entity_id: Any,
    reason: str,
    old: Any = None,
    new: Any = None,
) -> None:
    session.add(
        AuditLog(
            admin_id=admin_id,
            source=source,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            reason=require_reason(reason),
            old_value=_jsonable(old),
            new_value=_jsonable(new),
        )
    )


def _sub_dict(sub: Subscription | None) -> dict | None:
    if sub is None:
        return None
    return {
        "id": sub.id,
        "status": sub.status,
        "plan_code": sub.plan_code,
        "is_lifetime": sub.is_lifetime,
        "device_limit": sub.device_limit,
        "expires_at": sub.expires_at,
        "source": sub.source,
    }


# ---------- stats ----------

async def stats(session: AsyncSession) -> dict[str, Any]:
    now = utcnow()
    count = lambda stmt: session.scalar(stmt)  # noqa: E731
    # Revenue counts real money only; SpirCoin orders are reported in the economy block.
    paid_orders = select(Order).where(Order.status == OrderStatus.PAID, Order.currency != "SPC").subquery()
    return {
        "users_total": await count(select(func.count(User.id))),
        "users_new_24h": await count(select(func.count(User.id)).where(User.created_at > now - timedelta(days=1))),
        "users_new_7d": await count(select(func.count(User.id)).where(User.created_at > now - timedelta(days=7))),
        "subs_active": await count(select(func.count(Subscription.id)).where(Subscription.status == SubStatus.ACTIVE)),
        "subs_expired": await count(select(func.count(Subscription.id)).where(Subscription.status == SubStatus.EXPIRED)),
        "subs_suspended": await count(select(func.count(Subscription.id)).where(Subscription.status == SubStatus.SUSPENDED)),
        "subs_paid_active": await count(
            select(func.count(Subscription.id)).where(Subscription.status == SubStatus.ACTIVE, Subscription.source.in_(("payment", "admin", "bonus")))
        ),
        "subs_lifetime": await count(select(func.count(Subscription.id)).where(Subscription.is_lifetime.is_(True), Subscription.status == SubStatus.ACTIVE)),
        "revenue_minor": int(await count(select(func.coalesce(func.sum(paid_orders.c.amount_minor), 0))) or 0),
        "orders_paid": await count(select(func.count()).select_from(paid_orders)),
        "trials_total": await count(select(func.count(TrialActivation.id)).where(TrialActivation.status == "active")),
        "referrals_total": await count(select(func.count(Referral.id))),
        "referrals_with_trial": await count(select(func.count(Referral.trial_activated_at))),
        "referrals_with_payment": await count(select(func.count(Referral.first_paid_at))),
        "bonus_days_granted": int(
            await count(select(func.coalesce(func.sum(ReferralReward.days), 0)).where(ReferralReward.status == "granted")) or 0
        ),
        "users_banned": await count(select(func.count(User.id)).where(User.is_banned.is_(True))),
        "devices_active": await count(select(func.count(Device.id)).where(Device.status == DevStatus.ACTIVE)),
        "vpn_ops_pending": await count(select(func.count(VpnOperation.id)).where(VpnOperation.status == "pending")),
        "vpn_ops_failed_24h": await count(
            select(func.count(VpnOperation.id)).where(VpnOperation.status == "failed", VpnOperation.updated_at > now - timedelta(days=1))
        ),
    }


# ---------- users ----------

async def user_details(session: AsyncSession, user_id: int) -> dict[str, Any]:
    user = await session.get(User, user_id)
    if user is None:
        raise ServiceError("Пользователь не найден.", "not_found")
    sub = await subs_svc.get_current(session, user_id)
    devices = await dev_svc.list_devices(session, user_id, include_revoked=True)
    orders = list((await session.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(30))).scalars())
    bonus = await ref_svc.bonus_history(session, user_id, limit=50)
    ref_summary = await ref_svc.referral_summary(session, user_id)
    referrer = await session.scalar(select(Referral.referrer_id).where(Referral.referred_id == user_id))
    trial = await session.scalar(select(TrialActivation).where(TrialActivation.user_id == user_id))
    from backend.models import SubscriptionEvent, SupportTicket

    history = list(
        (await session.execute(select(SubscriptionEvent).where(SubscriptionEvent.user_id == user_id).order_by(SubscriptionEvent.created_at.desc()).limit(50))).scalars()
    )
    tickets = list((await session.execute(select(SupportTicket).where(SupportTicket.user_id == user_id).order_by(SupportTicket.updated_at.desc()).limit(30))).scalars())
    return {
        "subscription_history": history,
        "support_tickets": tickets,
        "user": user,
        "subscription": sub,
        "devices": devices,
        "orders": orders,
        "bonus": bonus,
        "referrals": ref_summary,
        "referrer_id": referrer,
        "trial": trial,
    }


async def _current_or_error(session: AsyncSession, user_id: int) -> Subscription:
    sub = await subs_svc.get_current(session, user_id, lock=True)
    if sub is None:
        raise ServiceError("У пользователя нет подписки.", "no_subscription")
    return sub


async def set_expiry(ctx: AppContext, admin_id: int, source: str, user_id: int, new_expires: datetime, reason: str) -> Subscription:
    reason = require_reason(reason)
    if new_expires.tzinfo is None:
        new_expires = new_expires.replace(tzinfo=timezone.utc)
    async with ctx.session() as session, session.begin():
        sub = await _current_or_error(session, user_id)
        old = _sub_dict(sub)
        await subs_svc.set_expiry(session, sub, new_expires, actor=f"admin:{admin_id}", comment=reason)
        audit(session, admin_id=admin_id, source=source, action="subscription.set_expiry", entity_type="subscription", entity_id=sub.id, reason=reason, old=old, new=_sub_dict(sub))
    await dev_svc.sync_subscription(ctx, sub.id, actor=f"admin:{admin_id}")
    return sub


async def add_days(ctx: AppContext, admin_id: int, source: str, user_id: int, days: int, reason: str, device_limit: int = 1) -> Subscription:
    """Extend by N days; if the user has no subscription, create an admin-granted one."""
    reason = require_reason(reason)
    if not 1 <= days <= 3650:
        raise ServiceError("Количество дней: от 1 до 3650.", "bad_days")
    async with ctx.session() as session, session.begin():
        sub = await subs_svc.get_current(session, user_id, lock=True)
        if await session.get(User, user_id) is None:
            raise ServiceError("Пользователь не найден.", "not_found")
        old = _sub_dict(sub)
        if sub is None:
            now = utcnow()
            sub = Subscription(
                user_id=user_id, plan_code="admin", status=SubStatus.ACTIVE, device_limit=max(1, min(device_limit, 50)), source="admin",
                starts_at=now, expires_at=now + timedelta(days=days),
            )
            session.add(sub)
            await session.flush()
            subs_svc.log_event(session, sub, "created", actor=f"admin:{admin_id}", days=days, comment=reason)
        else:
            await subs_svc.add_days(session, sub, days, action="admin_add_days", actor=f"admin:{admin_id}", comment=reason)
        audit(session, admin_id=admin_id, source=source, action="subscription.add_days", entity_type="subscription", entity_id=sub.id, reason=reason, old=old, new=_sub_dict(sub))
    await dev_svc.sync_subscription(ctx, sub.id, actor=f"admin:{admin_id}")
    return sub


async def set_device_limit(ctx: AppContext, admin_id: int, source: str, user_id: int, limit: int, reason: str) -> Subscription:
    reason = require_reason(reason)
    if not 1 <= limit <= 50:
        raise ServiceError("Лимит устройств: от 1 до 50.", "bad_limit")
    async with ctx.session() as session, session.begin():
        sub = await _current_or_error(session, user_id)
        old = _sub_dict(sub)
        sub.device_limit = limit
        subs_svc.log_event(session, sub, "admin_device_limit", actor=f"admin:{admin_id}", comment=reason)
        audit(session, admin_id=admin_id, source=source, action="subscription.device_limit", entity_type="subscription", entity_id=sub.id, reason=reason, old=old, new=_sub_dict(sub))
    return sub


async def suspend(ctx: AppContext, admin_id: int, source: str, user_id: int, reason: str, *, suspended: bool) -> tuple[int, int]:
    reason = require_reason(reason)
    async with ctx.session() as session, session.begin():
        sub = await _current_or_error(session, user_id)
        old = _sub_dict(sub)
        old_status = sub.status
        if suspended:
            if sub.status == SubStatus.SUSPENDED:
                raise ServiceError("Доступ уже приостановлен.", "already")
            sub.status, sub.suspended_reason = SubStatus.SUSPENDED, reason
        else:
            if sub.status != SubStatus.SUSPENDED:
                raise ServiceError("Доступ не был приостановлен.", "not_suspended")
            sub.status = SubStatus.ACTIVE if (sub.is_lifetime or (sub.expires_at and sub.expires_at > utcnow())) else SubStatus.EXPIRED
            sub.suspended_reason = None
        subs_svc.log_event(session, sub, "suspended" if suspended else "resumed", actor=f"admin:{admin_id}", old_status=old_status, comment=reason)
        audit(session, admin_id=admin_id, source=source, action="subscription.suspend" if suspended else "subscription.resume", entity_type="subscription", entity_id=sub.id, reason=reason, old=old, new=_sub_dict(sub))
    return await dev_svc.set_suspended(ctx, sub.id, suspended, actor=f"admin:{admin_id}")


async def revoke_device(ctx: AppContext, admin_id: int, source: str, device_id: int, reason: str) -> dev_svc.OpResult:
    reason = require_reason(reason)
    async with ctx.session() as session, session.begin():
        device = await session.get(Device, device_id)
        if device is None:
            raise ServiceError("Устройство не найдено.", "not_found")
        audit(session, admin_id=admin_id, source=source, action="device.revoke", entity_type="device", entity_id=device_id, reason=reason, old={"status": device.status}, new={"status": "revoking"})
    return await dev_svc.revoke_device(ctx, device_id, user_id=None, actor=f"admin:{admin_id}")


async def provision_for_user(ctx: AppContext, admin_id: int, source: str, user_id: int, node_code: str | None, reason: str) -> dev_svc.OpResult:
    reason = require_reason(reason)
    async with ctx.session() as session, session.begin():
        audit(session, admin_id=admin_id, source=source, action="device.create", entity_type="user", entity_id=user_id, reason=reason, new={"node": node_code})
    return await dev_svc.add_device(ctx, user_id, node_code=node_code, label="Добавлено администратором", actor=f"admin:{admin_id}")


# ---------- bonuses / referrals ----------

async def adjust_bonus(ctx: AppContext, admin_id: int, source: str, user_id: int, days: int, reason: str) -> int:
    reason = require_reason(reason)
    if days == 0 or abs(days) > 3650:
        raise ServiceError("Количество дней: от -3650 до 3650, не 0.", "bad_days")
    async with ctx.session() as session, session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ServiceError("Пользователь не найден.", "not_found")
        before = await ref_svc.bonus_balance(session, user_id)
        if before + days < 0:
            raise ServiceError(f"Баланс не может стать отрицательным (сейчас {before} дн.).", "negative_balance")
        session.add(BonusTransaction(user_id=user_id, days=days, kind="admin_adjust", admin_id=admin_id, comment=reason))
        audit(session, admin_id=admin_id, source=source, action="bonus.adjust", entity_type="user", entity_id=user_id, reason=reason, old={"balance": before}, new={"balance": before + days})
        return before + days


async def reverse_reward(ctx: AppContext, admin_id: int, source: str, reward_id: int, reason: str) -> ReferralReward:
    reason = require_reason(reason)
    async with ctx.session() as session, session.begin():
        reward = await session.get(ReferralReward, reward_id, with_for_update=True)
        if reward is None:
            raise ServiceError("Начисление не найдено.", "not_found")
        if reward.status == "reversed":
            raise ServiceError("Начисление уже отменено.", "already")
        reward.status, reward.reversed_at, reward.reversed_by, reward.reverse_reason = "reversed", utcnow(), admin_id, reason
        await session.execute(
            insert(BonusTransaction)
            .values(user_id=reward.referrer_id, days=-reward.days, kind="reversal", referral_reward_id=reward.id, admin_id=admin_id, comment=reason)
            .on_conflict_do_nothing()
        )
        audit(session, admin_id=admin_id, source=source, action="referral.reverse_reward", entity_type="referral_reward", entity_id=reward.id, reason=reason, old={"status": "granted", "days": reward.days}, new={"status": "reversed"})
    return reward


async def reward_history(session: AsyncSession, limit: int = 100) -> list[ReferralReward]:
    return list((await session.execute(select(ReferralReward).order_by(ReferralReward.created_at.desc()).limit(limit))).scalars())


# ---------- plans / settings ----------

PLAN_FIELDS = {"title", "description", "price_minor", "price_coins", "duration_days", "device_limit", "is_active", "is_lifetime", "sort_order"}


def _plan_dict(p: Plan) -> dict:
    return {f: getattr(p, f) for f in sorted(PLAN_FIELDS)} | {"code": p.code}


def _validate_plan(values: dict[str, Any]) -> None:
    if "price_minor" in values and (not isinstance(values["price_minor"], int) or not 100 <= values["price_minor"] <= 10_000_000):
        raise ServiceError("Цена: от 1 до 100 000 ₽.", "bad_price")
    if "price_coins" in values and values["price_coins"] is not None and (
        not isinstance(values["price_coins"], int) or not 1 <= values["price_coins"] <= 100_000_000
    ):
        raise ServiceError("Цена в SpirCoin: от 1 до 100 000 000 или пусто (нельзя купить за монеты).", "bad_price")
    if "device_limit" in values and (not isinstance(values["device_limit"], int) or not 1 <= values["device_limit"] <= 50):
        raise ServiceError("Лимит устройств: от 1 до 50.", "bad_limit")
    if values.get("duration_days") is not None and (not isinstance(values["duration_days"], int) or not 1 <= values["duration_days"] <= 3650):
        raise ServiceError("Срок: от 1 до 3650 дней.", "bad_duration")
    if "title" in values and not (1 <= len(str(values["title"]).strip()) <= 64):
        raise ServiceError("Название: от 1 до 64 символов.", "bad_title")


async def update_plan(ctx: AppContext, admin_id: int, source: str, plan_id: int, changes: dict[str, Any], reason: str) -> Plan:
    reason = require_reason(reason)
    changes = {k: v for k, v in changes.items() if k in PLAN_FIELDS}
    if not changes:
        raise ServiceError("Нет изменений.", "no_changes")
    _validate_plan(changes)
    async with ctx.session() as session, session.begin():
        plan = await session.get(Plan, plan_id, with_for_update=True)
        if plan is None:
            raise ServiceError("Тариф не найден.", "not_found")
        old = _plan_dict(plan)
        for k, v in changes.items():
            setattr(plan, k, v)
        if plan.is_lifetime:
            plan.duration_days = None
        elif not plan.duration_days:
            raise ServiceError("Для ограниченного тарифа нужен срок в днях.", "bad_duration")
        audit(session, admin_id=admin_id, source=source, action="plan.update", entity_type="plan", entity_id=plan.id, reason=reason, old=old, new=_plan_dict(plan))
    return plan


async def create_plan(ctx: AppContext, admin_id: int, source: str, values: dict[str, Any], reason: str) -> Plan:
    reason = require_reason(reason)
    code = str(values.get("code") or "").strip().lower()
    if not code or not code.replace("_", "").isalnum() or len(code) > 32:
        raise ServiceError("Код тарифа: латиница, цифры и _, до 32 символов.", "bad_code")
    data = {k: v for k, v in values.items() if k in PLAN_FIELDS}
    data.setdefault("is_lifetime", False)
    if data["is_lifetime"]:
        data["duration_days"] = None
    _validate_plan(data)
    async with ctx.session() as session, session.begin():
        if await session.scalar(select(Plan.id).where(Plan.code == code)):
            raise ServiceError("Тариф с таким кодом уже существует.", "duplicate")
        plan = Plan(code=code, **data)
        session.add(plan)
        await session.flush()
        audit(session, admin_id=admin_id, source=source, action="plan.create", entity_type="plan", entity_id=plan.id, reason=reason, new=_plan_dict(plan))
    return plan


async def update_setting(ctx: AppContext, admin_id: int, source: str, key: str, value: Any, reason: str) -> Any:
    from backend.services import roles

    reason = require_reason(reason)
    if key in settings_svc.OWNER_ONLY and not await roles.is_owner(ctx, admin_id):
        raise ServiceError("Эту настройку экономики может менять только владелец.", "forbidden")
    async with ctx.session() as session, session.begin():
        old, new = await settings_svc.put(session, key, value)
        audit(session, admin_id=admin_id, source=source, action="setting.update", entity_type="setting", entity_id=key, reason=reason, old={"value": old}, new={"value": new})
    return new


async def recent_audit(session: AsyncSession, limit: int = 100) -> list[AuditLog]:
    return list((await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))).scalars())


async def recent_vpn_ops(session: AsyncSession, *, only_problems: bool = False, limit: int = 100) -> list[VpnOperation]:
    stmt = select(VpnOperation).order_by(VpnOperation.updated_at.desc()).limit(limit)
    if only_problems:
        stmt = stmt.where(VpnOperation.status.in_(("pending", "failed")))
    return list((await session.execute(stmt)).scalars())


async def nodes_overview(session: AsyncSession) -> list[dict]:
    nodes = (await session.execute(select(VpnNode).order_by(VpnNode.id))).scalars().all()
    counts = dict(
        (await session.execute(select(Device.node_id, func.count(Device.id)).where(Device.status == DevStatus.ACTIVE).group_by(Device.node_id))).all()
    )
    return [
        {
            "id": n.id,
            "code": n.code,
            "name": n.name,
            "country_code": n.country_code,
            "flag": n.flag,
            "is_active": n.is_active,
            "health_ok": n.last_health_ok,
            "health_at": n.last_health_at,
            "last_error": n.last_error,
            "remote_clients": n.remote_clients,
            "active_devices": counts.get(n.id, 0),
        }
        for n in nodes
    ]


async def reset_support_limit(ctx: AppContext, admin_id: int, source: str, user_id: int, reason: str) -> int:
    """Lets the user create new tickets again right away. Returns how many tickets were used before the reset."""
    from backend.models import AppSetting
    from backend.services import support as support_svc

    reason = require_reason(reason)
    async with ctx.session() as session, session.begin():
        if await session.get(User, user_id) is None:
            raise ServiceError("Пользователь не найден.", "not_found")
    used, _ = await support_svc.daily_usage(ctx, user_id)
    async with ctx.session() as session, session.begin():
        key = support_svc._reset_key(user_id)
        row = await session.get(AppSetting, key)
        stamp = utcnow().isoformat()
        if row is None:
            session.add(AppSetting(key=key, value=stamp))
        else:
            row.value = stamp
        audit(session, admin_id=admin_id, source=source, action="support.limit_reset", entity_type="user", entity_id=user_id, reason=reason, old={"used": used}, new={"used": 0})
    return used

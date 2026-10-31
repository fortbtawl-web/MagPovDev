"""Standard trial period. Counted as activated only after the node confirms the client was created."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from backend.models import Order, OrderStatus, Subscription, SubStatus, TrialActivation, TrialCodeActivation, User, VpnOperation
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services import settings as settings_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow


@dataclass
class TrialInfo:
    available: bool
    days: int
    reason: str | None = None


async def trial_info(ctx: AppContext, user_id: int) -> TrialInfo:
    async with ctx.session() as session:
        days = int(await settings_svc.get(session, "trial_days"))
        user = await session.get(User, user_id)
        if user is not None and user.is_banned:
            return TrialInfo(False, days, "Аккаунт заблокирован. Обратитесь в поддержку.")
        if not await settings_svc.get(session, "trial_enabled"):
            return TrialInfo(False, days, "Пробный период сейчас недоступен.")
        code_trial = await session.scalar(select(TrialCodeActivation.status).where(TrialCodeActivation.user_id == user_id))
        if code_trial in ("active", "pending"):
            return TrialInfo(False, days, "Вы уже получили тестовую подписку по коду — обычный пробный период недоступен.")
        trial = await session.scalar(select(TrialActivation).where(TrialActivation.user_id == user_id))
        if trial and trial.status == "active":
            return TrialInfo(False, days, "Вы уже использовали пробный период.")
        if trial and trial.status == "pending":
            return TrialInfo(False, days, "Пробный период уже активируется, подождите немного.")
        paid = await session.scalar(
            select(func.count(Order.id)).where(Order.user_id == user_id, Order.status.in_((OrderStatus.PAID, OrderStatus.REFUNDED)))
        )
        if paid:
            return TrialInfo(False, days, "Пробный период доступен только новым пользователям без оплаченных подписок.")
        if await subs_svc.get_current(session, user_id):
            return TrialInfo(False, days, "У вас уже есть подписка.")
        return TrialInfo(True, days)


async def activate_trial(ctx: AppContext, user_id: int) -> dev_svc.OpResult:
    info = await trial_info(ctx, user_id)
    if not info.available:
        raise ServiceError(info.reason or "Пробный период недоступен.", "trial_unavailable")

    async with ctx.session() as session, session.begin():
        # Serialises concurrent activations for the same user.
        await session.execute(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
        trial = await session.scalar(select(TrialActivation).where(TrialActivation.user_id == user_id).with_for_update())
        if trial is not None and trial.status != "failed":
            raise ServiceError("Вы уже использовали пробный период.", "trial_used")
        if await session.scalar(select(TrialCodeActivation.id).where(TrialCodeActivation.user_id == user_id, TrialCodeActivation.status.in_(("active", "pending")))):
            raise ServiceError("Вы уже получили тестовую подписку по коду — обычный пробный период недоступен.", "trial_used")
        if await subs_svc.get_current(session, user_id):
            raise ServiceError("У вас уже есть подписка.", "has_subscription")
        node = await dev_svc.get_node(session, None)
        days = int(await settings_svc.get(session, "trial_days"))
        sub = Subscription(user_id=user_id, plan_code="trial", status=SubStatus.PENDING, device_limit=1, source="trial")
        session.add(sub)
        await session.flush()
        if trial is None:
            trial = TrialActivation(user_id=user_id, status="pending", days=days, subscription_id=sub.id)
            session.add(trial)
        else:
            trial.status, trial.days, trial.subscription_id, trial.error = "pending", days, sub.id, None
        device = await dev_svc.reserve_device(session, sub, node, "Пробное устройство", allow_pending_sub=True)
        sub_id, device_id = sub.id, device.id

    expires = utcnow() + timedelta(days=days)
    result = await dev_svc.provision_device(ctx, device_id, expires_at=expires, actor="user")

    async with ctx.session() as session, session.begin():
        sub = await session.get(Subscription, sub_id, with_for_update=True)
        trial = await session.scalar(select(TrialActivation).where(TrialActivation.user_id == user_id).with_for_update())
        if result.confirmed:
            sub.status, sub.starts_at, sub.expires_at = SubStatus.ACTIVE, utcnow(), expires
            trial.status, trial.activated_at = "active", utcnow()
            await session.flush()
            subs_svc.log_event(session, sub, "created", actor="user", old_status=SubStatus.PENDING, days=days, comment="trial")
            await ref_svc.grant_reward(session, user_id, "trial_activated")
            return result
        sub.status = SubStatus.FAILED
        trial.status, trial.error = "failed", (result.error or "")[:500]
        device = await session.get(type(result.device), device_id, with_for_update=True)
        if device.status == "pending":
            # Outcome unknown (timeout): stop retrying create and make sure nothing lingers on the node.
            device.status = "failed"
            for op in (await session.execute(select(VpnOperation).where(VpnOperation.device_id == device_id, VpnOperation.status == "pending"))).scalars():
                op.status, op.last_error = "failed", "trial aborted"
            await dev_svc.record_op(session, device=device, node_id=device.node_id, op_type="revoke", status="pending", error="cleanup after failed trial")
    raise ServiceError(
        "Не удалось создать подключение: VPN-сервер не подтвердил операцию. Пробный период не израсходован — попробуйте позже.",
        "vpn_failed",
    )

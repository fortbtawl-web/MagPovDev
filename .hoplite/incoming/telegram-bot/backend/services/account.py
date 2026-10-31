"""User-facing account operations shared by the bot and the Mini App."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from backend.models import BonusTransaction, Subscription, SubStatus, User
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow


async def apply_bonus_days(ctx: AppContext, user_id: int, days: int | None = None) -> Subscription:
    """Spend bonus days: extends the current limited subscription, or starts a 1-device bonus subscription."""
    async with ctx.session() as session, session.begin():
        await session.execute(select(User.id).where(User.id == user_id).with_for_update())
        balance = await ref_svc.bonus_balance(session, user_id)
        days = balance if days is None else days
        if days <= 0 or days > balance:
            raise ServiceError(f"Недостаточно бонусных дней (доступно {balance}).", "no_bonus")
        sub = await subs_svc.get_current(session, user_id, lock=True)
        if sub is not None and sub.is_lifetime:
            raise ServiceError("У вас бессрочная подписка — бонусные дни не нужны.", "lifetime")
        if sub is not None and sub.status in (SubStatus.PENDING, SubStatus.SUSPENDED):
            raise ServiceError("Сейчас нельзя применить бонус: подписка в обработке или приостановлена.", "bad_state")
        if sub is None:
            now = utcnow()
            sub = Subscription(
                user_id=user_id, plan_code="bonus", status=SubStatus.ACTIVE, device_limit=1, source="bonus",
                starts_at=now, expires_at=now + timedelta(days=days),
            )
            session.add(sub)
            await session.flush()
            subs_svc.log_event(session, sub, "created", actor="user", days=days, comment="bonus days")
        else:
            await subs_svc.add_days(session, sub, days, action="bonus_applied", actor="user")
        session.add(BonusTransaction(user_id=user_id, days=-days, kind="applied", subscription_id=sub.id))
    await dev_svc.sync_subscription(ctx, sub.id, actor="user")
    return sub


async def overview(ctx: AppContext, user_id: int) -> dict[str, Any]:
    from backend.services import trial as trial_svc

    async with ctx.session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise ServiceError("Пользователь не найден. Нажмите /start в боте.", "not_found")
        sub = await subs_svc.get_current(session, user_id)
        devices = await dev_svc.list_devices(session, user_id)
        balance = await ref_svc.bonus_balance(session, user_id)
    trial = await trial_svc.trial_info(ctx, user_id)
    return {"user": user, "subscription": sub, "devices": devices, "bonus_balance": balance, "trial": trial}

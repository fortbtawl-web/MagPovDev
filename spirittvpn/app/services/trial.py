"""Free trial: once per Telegram account, enforced by UNIQUE(trial_activations.user_id)."""
from __future__ import annotations

import enum
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, TrialActivation, User
from app.services import settings_store
from app.services.users import extend_subscription, get_subscription, has_accepted_terms, has_paid_orders, trial_used
from app.services.vpn_sync import apply_vpn_state
from app.vpn.base import VPNError, VPNProvider

log = logging.getLogger("spirittvpn.trial")


class TrialResult(enum.Enum):
    ACTIVATED = "activated"
    ALREADY_USED = "already_used"
    NOT_ELIGIBLE = "not_eligible"
    DISABLED = "disabled"
    NEED_TERMS = "need_terms"
    SERVICE_NOT_READY = "service_not_ready"
    VPN_UNAVAILABLE = "vpn_unavailable"


async def activate_trial(
    session: AsyncSession, user: User, provider: VPNProvider, legal_version: str, now: datetime, vpn_attempts: int = 3
) -> tuple[TrialResult, Subscription | None]:
    if not await settings_store.get_bool(session, "trial_enabled"):
        return TrialResult.DISABLED, None
    if await trial_used(session, user.id):
        return TrialResult.ALREADY_USED, None
    if await get_subscription(session, user.id) is not None or await has_paid_orders(session, user.id):
        return TrialResult.NOT_ELIGIBLE, None
    if not provider.is_configured:
        return TrialResult.SERVICE_NOT_READY, None
    if not await has_accepted_terms(session, user, legal_version):
        return TrialResult.NEED_TERMS, None

    days = await settings_store.get_int(session, "trial_days")
    session.add(TrialActivation(user_id=user.id, activated_at=now, expires_at=now + timedelta(days=days)))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return TrialResult.ALREADY_USED, None

    sub = await extend_subscription(session, user, days, now, is_trial=True)
    try:
        # Access is created before commit: if the VPN fails, the trial is not spent.
        await apply_vpn_state(session, provider, user, sub, now, vpn_attempts)
    except VPNError as exc:
        log.warning("trial rollback for user_id=%s: %s", user.id, type(exc).__name__)
        await session.rollback()
        return TrialResult.VPN_UNAVAILABLE, None
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return TrialResult.ALREADY_USED, None
    return TrialResult.ACTIVATED, sub

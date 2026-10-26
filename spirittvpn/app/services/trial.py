"""Free trial: once per Telegram account (UNIQUE trial_activations.user_id) with the first device created atomically."""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BonusSource, Device, Subscription, TrialActivation, User
from app.services import settings_store
from app.services.devices import _compensate, log_vpn_error, provision_device
from app.services.referrals import Reward, grant_referral_bonus
from app.services.users import extend_subscription, get_subscription, has_accepted_terms, has_paid_orders, trial_used
from app.vpn.base import VPNError, VPNService

log = logging.getLogger("spirittvpn.trial")


class TrialResult(enum.Enum):
    ACTIVATED = "activated"
    ALREADY_USED = "already_used"
    NOT_ELIGIBLE = "not_eligible"
    DISABLED = "disabled"
    NEED_TERMS = "need_terms"
    SERVICE_NOT_READY = "service_not_ready"
    VPN_UNAVAILABLE = "vpn_unavailable"


@dataclass
class TrialOutcome:
    result: TrialResult
    subscription: Subscription | None = None
    device: Device | None = None
    reward: Reward | None = None


async def activate_trial(
    session: AsyncSession, user: User, vpn: VPNService, legal_version: str, now: datetime,
    *, device_limit: int = 1, prefix: str = "sp",
) -> TrialOutcome:
    if not await settings_store.get_bool(session, "trial_enabled"):
        return TrialOutcome(TrialResult.DISABLED)
    if await trial_used(session, user.id):
        return TrialOutcome(TrialResult.ALREADY_USED)
    if await get_subscription(session, user.id) is not None or await has_paid_orders(session, user.id):
        return TrialOutcome(TrialResult.NOT_ELIGIBLE)
    if not vpn.is_configured:
        return TrialOutcome(TrialResult.SERVICE_NOT_READY)
    if not await has_accepted_terms(session, user, legal_version):
        return TrialOutcome(TrialResult.NEED_TERMS)

    days = await settings_store.get_int(session, "trial_days")
    session.add(TrialActivation(user_id=user.id, activated_at=now, expires_at=now + timedelta(days=days)))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return TrialOutcome(TrialResult.ALREADY_USED)

    sub = await extend_subscription(session, user, days, now, is_trial=True, device_limit=device_limit)
    try:
        # Access is created before commit: if the panel fails, the trial is not spent.
        device = await provision_device(session, vpn, user, sub, "Моё устройство", prefix)
    except VPNError as exc:
        log.warning("trial rollback for user_id=%s: %s", user.id, type(exc).__name__)
        await session.rollback()
        await log_vpn_error(session, "create_client", exc)
        return TrialOutcome(TrialResult.VPN_UNAVAILABLE)
    sub.vpn_sync_needed = False
    reward = await grant_referral_bonus(session, user, BonusSource.REFERRAL_TRIAL, now)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        await _compensate(vpn, device)
        return TrialOutcome(TrialResult.ALREADY_USED)
    return TrialOutcome(TrialResult.ACTIVATED, sub, device, reward)

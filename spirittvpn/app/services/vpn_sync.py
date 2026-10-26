"""Keeps the VPN side in line with the subscription stored in the DB."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription, User, VPNAccount
from app.services.users import is_active
from app.vpn.base import VPNError, VPNNotConfiguredError, VPNProvider
from app.vpn.retry import with_retries

log = logging.getLogger("spirittvpn.vpn_sync")


async def _upsert_account(session: AsyncSession, user: User, provider: VPNProvider, external_id: str, status: str):
    acc = await session.scalar(select(VPNAccount).where(VPNAccount.user_id == user.id))
    if acc is None:
        session.add(VPNAccount(user_id=user.id, provider=provider.name, external_id=external_id, status=status))
    else:
        acc.provider, acc.status = provider.name, status
        if external_id:
            acc.external_id = external_id


async def apply_vpn_state(
    session: AsyncSession, provider: VPNProvider, user: User, sub: Subscription, now: datetime, attempts: int = 3
) -> None:
    """Pushes the desired state to the VPN provider. Raises VPNError; does not commit."""
    if is_active(sub, now):
        state = await with_retries(
            lambda: provider.ensure_client(user.telegram_id, sub.expires_at), attempts=attempts, op_name="ensure_client"
        )
        await _upsert_account(session, user, provider, state.external_id, "active")
    else:
        await with_retries(lambda: provider.suspend(user.telegram_id), attempts=attempts, op_name="suspend")
        await _upsert_account(session, user, provider, "", "suspended")
    sub.vpn_sync_needed = False
    sub.vpn_sync_attempts = 0
    sub.vpn_last_error = None


async def sync_user_vpn(
    session: AsyncSession, provider: VPNProvider, user: User, sub: Subscription, now: datetime, attempts: int = 3
) -> bool:
    """Best-effort sync with commit. On failure the flag stays set and the job retries later."""
    if not provider.is_configured:
        return False
    try:
        await apply_vpn_state(session, provider, user, sub, now, attempts)
        ok = True
    except VPNNotConfiguredError:
        return False
    except VPNError as exc:
        sub.vpn_sync_attempts += 1
        sub.vpn_last_error = type(exc).__name__
        log.warning("vpn sync failed for user_id=%s: %s", user.id, type(exc).__name__)
        ok = False
    await session.commit()
    return ok

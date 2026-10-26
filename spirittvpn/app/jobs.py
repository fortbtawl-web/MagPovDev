"""Periodic background jobs. Each step is idempotent, so restarts or overlapping runs never add days twice."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import func, select

from app import keyboards as kb
from app.models import Subscription
from app.runtime import Deps, make_notifier, notify_admins
from app.services.ops import Notifier, expire_subscriptions, reconcile_vpn, retry_vpn_sync, send_expiry_notices
from app.services.orders import cancel_stale_orders

log = logging.getLogger("spirittvpn.jobs")


async def run_jobs_once(deps: Deps, notify: Notifier, now: datetime, *, reconcile: bool) -> dict[str, int]:
    s = deps.settings
    stats: dict[str, int] = {}
    async with deps.session_factory() as session:
        stats["orders_cancelled"] = await cancel_stale_orders(session, s.order_ttl_hours, now)
    async with deps.session_factory() as session:
        stats["notices"] = await send_expiry_notices(session, notify, now, s.final_notice_hours, deps.fmt_dt, kb.expiring())
    async with deps.session_factory() as session:
        stats["expired"] = await expire_subscriptions(session, deps.vpn, notify, now, kb.expiring(), s.vpn_retry_attempts)
    if reconcile:
        async with deps.session_factory() as session:
            stats["reconcile_mismatches"] = await reconcile_vpn(session, deps.vpn, now)
    async with deps.session_factory() as session:
        stats["vpn_synced"], stats["vpn_failed"] = await retry_vpn_sync(
            session, deps.vpn, now, s.vpn_max_sync_attempts, s.vpn_retry_attempts
        )
    return stats


async def scheduler(deps: Deps, bot: Bot) -> None:
    notify = make_notifier(bot)
    last_reconcile = 0.0
    last_stuck_alert = 0.0
    while True:
        try:
            reconcile = time.monotonic() - last_reconcile >= deps.settings.reconcile_interval_seconds
            stats = await run_jobs_once(deps, notify, datetime.now(UTC), reconcile=reconcile)
            if reconcile:
                last_reconcile = time.monotonic()
            if any(stats.values()):
                log.info("jobs: %s", stats)
            async with deps.session_factory() as session:
                stuck = await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.vpn_sync_needed, Subscription.vpn_sync_attempts >= deps.settings.vpn_max_sync_attempts
                    )
                )
            if stuck and deps.vpn.is_configured and time.monotonic() - last_stuck_alert > 6 * 3600:
                last_stuck_alert = time.monotonic()
                await notify_admins(bot, deps.settings, f"🚨 VPN: {stuck} подписок не удаётся синхронизировать. Проверь сервер.")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive DB/VPN outages
            log.exception("background jobs iteration failed")
        await asyncio.sleep(deps.settings.job_interval_seconds)

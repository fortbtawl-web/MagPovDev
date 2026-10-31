"""Background jobs: expiry, payment polling, VPN retries/reconciliation, node health.

Each job takes a PostgreSQL advisory lock so several processes can run the worker safely.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services import subscriptions as subs_svc
from backend.services.context import AppContext

log = logging.getLogger("spiritt.worker")
LOCK_IDS = {"expire": 7301, "payments": 7302, "vpn_retry": 7303, "reconcile": 7304, "health": 7305, "fulfill": 7306}


async def _locked(ctx: AppContext, name: str, coro_factory) -> None:
    async with ctx.session() as session:
        conn = await session.connection()
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_IDS[name]})).scalar()
        if not got:
            return
        try:
            await coro_factory()
        except Exception:  # noqa: BLE001
            log.exception("job %s failed", name)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_IDS[name]})
            await session.commit()


async def expire_job(ctx: AppContext) -> None:
    async with ctx.session() as session, session.begin():
        ids = await subs_svc.expire_due(session)
    if ids:
        log.info("expired %d subscriptions", len(ids))
        from backend.models import Subscription

        async with ctx.session() as session:
            users = [(await session.get(Subscription, i)).user_id for i in ids]
        for uid in users:
            await ctx.notifier.send(uid, "⌛ <b>Подписка SpirittVPN закончилась</b>\n━━━━━━━━━━━━━━━━\nVPN на ваших устройствах больше не подключается.\n\nПродлите подписку в разделе «💎 Тарифы» — доступ восстановится автоматически, перенастраивать приложение не нужно.")


async def run_once(ctx: AppContext, *, tick: int = 0) -> None:
    await _locked(ctx, "expire", lambda: expire_job(ctx))
    await _locked(ctx, "payments", lambda: pay_svc.poll_pending(ctx))
    await _locked(ctx, "fulfill", lambda: pay_svc.retry_fulfillment(ctx))
    await _locked(ctx, "vpn_retry", lambda: dev_svc.retry_pending(ctx))
    if tick % 5 == 0:
        await _locked(ctx, "health", lambda: dev_svc.check_nodes(ctx))
    if tick % 10 == 0:
        await _locked(ctx, "reconcile", lambda: dev_svc.reconcile(ctx))


async def run_forever(ctx: AppContext, stop: asyncio.Event) -> None:
    from backend.services import broadcast as broadcast_svc

    tick = 0
    log.info("worker started")
    # Broadcasts run in their own task: a long mass send must not delay expiry, payments or VPN retries.
    sender = asyncio.create_task(broadcast_svc.run_forever(ctx, stop))
    while not stop.is_set():
        await run_once(ctx, tick=tick)
        tick += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=ctx.settings.worker_interval_seconds)
        except asyncio.TimeoutError:
            pass
    await sender
    log.info("worker stopped")

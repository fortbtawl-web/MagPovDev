"""Admin web panel API. Every route requires an admin session; every mutation requires a reason and is audited."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from backend.api import serializers as ser
from backend.api.deps import AdminId, Ctx, OwnerId, StaffId, client_ip, limiter, require_perm
from backend.services import coins as coins_svc
from backend.services import grants as grants_svc
from backend.services import tasks as tasks_svc
from backend.services import roles
from backend.services import support as support_svc
from backend.models import Order, Plan, Subscription, User
from backend.services import admin as admin_svc
from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services import settings as settings_svc
from backend.services.auth import issue_token, request_admin_code, verify_admin_code

router = APIRouter(prefix="/api/admin")
SRC = "web"


class CodeRequestIn(BaseModel):
    telegram_id: int


class CodeVerifyIn(BaseModel):
    telegram_id: int
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DaysIn(ReasonIn):
    days: int = Field(ge=1, le=3650)
    device_limit: int = Field(default=1, ge=1, le=50)


class ExpiryIn(ReasonIn):
    expires_at: datetime


class LimitIn(ReasonIn):
    limit: int = Field(ge=1, le=50)


class BonusIn(ReasonIn):
    days: int = Field(ge=-3650, le=3650)


class NodeIn(ReasonIn):
    node_code: str | None = None


class PlanPatchIn(ReasonIn):
    changes: dict[str, Any]


class PlanCreateIn(ReasonIn):
    code: str
    title: str
    description: str = ""
    price_minor: int
    duration_days: int | None = None
    is_lifetime: bool = False
    device_limit: int = 1
    is_active: bool = True
    sort_order: int = 100


class SettingIn(ReasonIn):
    value: Any


@router.post("/auth/request-code")
async def auth_request_code(body: CodeRequestIn, request: Request, ctx: Ctx) -> dict:
    limiter.check(f"admin_code:{client_ip(request)}", 5, 600)
    await request_admin_code(ctx, body.telegram_id)
    # Same answer for admins and non-admins: don't reveal who is an admin.
    return {"ok": True, "message": "Если этот Telegram ID есть в списке администраторов, бот отправил код."}


@router.post("/auth/verify")
async def auth_verify(body: CodeVerifyIn, request: Request, ctx: Ctx) -> dict:
    limiter.check(f"admin_verify:{client_ip(request)}", 10, 600)
    if not await verify_admin_code(ctx, body.telegram_id, body.code):
        raise HTTPException(401, "Неверный или просроченный код")
    s = ctx.settings
    return {
        "token": issue_token(s.session_secret.get_secret_value(), user_id=body.telegram_id, role="admin", ttl_minutes=s.admin_session_ttl_minutes),
        "expires_in": s.admin_session_ttl_minutes * 60,
    }


@router.get("/me")
async def admin_me(admin_id: StaffId, ctx: Ctx) -> dict:
    role = await roles.get_role(ctx, admin_id)
    return {"admin_id": admin_id, "role": role, "is_owner": role == "owner", "permissions": sorted(await roles.effective_perms(ctx, admin_id))}


@router.get("/stats")
async def stats(admin_id: StaffId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        data = await admin_svc.stats(session)
        economy = await coins_svc.economy_stats(session)
        nodes = await admin_svc.nodes_overview(session)
        problems = await admin_svc.recent_vpn_ops(session, only_problems=True, limit=10)
        audit = await admin_svc.recent_audit(session, limit=10)
    data.update(await support_svc.counts(ctx))
    economy["trials"] = data.get("trials_total", 0)
    return {
        "stats": data,
        "economy": economy,
        "nodes": [{**n, "health_at": ser.ts(n["health_at"])} for n in nodes],
        "recent_problems": [ser.vpn_op(o) for o in problems],
        "recent_audit": [ser.audit(a) for a in audit],
    }


@router.get("/users")
async def users(ctx: Ctx, q: str = "", limit: int = 50, admin_id: int = require_perm("users.view")) -> dict:
    async with ctx.session() as session:
        rows = await admin_svc.find_users(session, q[:64], limit=max(1, min(limit, 200)))
    return {"users": [ser.user(u, await roles.get_role(ctx, u.id)) for u in rows]}


@router.get("/users/{user_id}")
async def user_detail(user_id: int, ctx: Ctx, admin_id: int = require_perm("users.view")) -> dict:
    async with ctx.session() as session:
        d = await admin_svc.user_details(session, user_id)
        coin_history = await coins_svc.history(session, user_id, limit=50)
        claims = await tasks_svc.user_claims(session, user_id)
    used = sum(1 for x in d["devices"] if x.status in ("pending", "active", "expired", "suspended", "revoking"))
    return {
        "user": ser.user(d["user"], await roles.get_role(ctx, user_id)),
        "subscription_history": [ser.sub_event(e, admin=True) for e in d["subscription_history"]],
        "support_tickets": [ser.ticket(t, admin_user=d["user"]) for t in d["support_tickets"]],
        "subscription": ser.subscription(d["subscription"], used),
        "devices": [ser.device(x) for x in d["devices"]],
        "orders": [ser.order(o) for o in d["orders"]],
        "bonus": [ser.bonus(b) for b in d["bonus"]],
        "referrals": d["referrals"],
        "referrer_id": d["referrer_id"],
        "trial": {"status": d["trial"].status, "activated_at": ser.ts(d["trial"].activated_at)} if d["trial"] else None,
        "coins": d["user"].coin_balance,
        "coin_history": [ser.coin_tx(t) for t in coin_history],
        "task_claims": claims,
    }


@router.post("/users/{user_id}/add-days")
async def add_days(user_id: int, body: DaysIn, admin_id: AdminId, ctx: Ctx) -> dict:
    sub = await admin_svc.add_days(ctx, admin_id, SRC, user_id, body.days, body.reason, body.device_limit)
    return {"subscription": ser.subscription(sub)}


@router.post("/users/{user_id}/set-expiry")
async def set_expiry(user_id: int, body: ExpiryIn, admin_id: AdminId, ctx: Ctx) -> dict:
    sub = await admin_svc.set_expiry(ctx, admin_id, SRC, user_id, body.expires_at, body.reason)
    return {"subscription": ser.subscription(sub)}


@router.post("/users/{user_id}/device-limit")
async def device_limit(user_id: int, body: LimitIn, admin_id: AdminId, ctx: Ctx) -> dict:
    sub = await admin_svc.set_device_limit(ctx, admin_id, SRC, user_id, body.limit, body.reason)
    return {"subscription": ser.subscription(sub)}


@router.post("/users/{user_id}/suspend")
async def suspend(user_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    ok, failed = await admin_svc.suspend(ctx, admin_id, SRC, user_id, body.reason, suspended=True)
    return {"confirmed": ok, "failed": failed}


@router.post("/users/{user_id}/resume")
async def resume(user_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    ok, failed = await admin_svc.suspend(ctx, admin_id, SRC, user_id, body.reason, suspended=False)
    return {"confirmed": ok, "failed": failed}


@router.post("/users/{user_id}/bonus")
async def bonus(user_id: int, body: BonusIn, admin_id: AdminId, ctx: Ctx) -> dict:
    balance = await admin_svc.adjust_bonus(ctx, admin_id, SRC, user_id, body.days, body.reason)
    return {"balance": balance}


@router.post("/users/{user_id}/devices")
async def create_device(user_id: int, body: NodeIn, admin_id: AdminId, ctx: Ctx) -> dict:
    res = await admin_svc.provision_for_user(ctx, admin_id, SRC, user_id, body.node_code, body.reason)
    return {"confirmed": res.confirmed, "error": res.error, "device": ser.device(res.device)}


@router.post("/devices/{device_id}/revoke")
async def revoke(device_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    res = await admin_svc.revoke_device(ctx, admin_id, SRC, device_id, body.reason)
    return {"confirmed": res.confirmed, "error": res.error, "device": ser.device(res.device)}


@router.post("/subscriptions/{sub_id}/sync")
async def sync(sub_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    async with ctx.session() as session, session.begin():
        admin_svc.audit(session, admin_id=admin_id, source=SRC, action="subscription.sync", entity_type="subscription", entity_id=sub_id, reason=body.reason)
    ok, failed = await dev_svc.sync_subscription(ctx, sub_id, actor=f"admin:{admin_id}")
    return {"ok": ok, "failed": failed}


@router.get("/subscriptions")
async def subscriptions(admin_id: AdminId, ctx: Ctx, status: str = "", limit: int = 100) -> dict:
    stmt = select(Subscription).order_by(Subscription.updated_at.desc()).limit(max(1, min(limit, 500)))
    if status:
        stmt = stmt.where(Subscription.status == status)
    async with ctx.session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        out = []
        for s in rows:
            out.append(ser.subscription(s, await dev_svc.occupied_slots(session, s.id)))
    return {"subscriptions": out}


@router.get("/orders")
async def orders(admin_id: AdminId, ctx: Ctx, status: str = "", limit: int = 100) -> dict:
    stmt = select(Order).order_by(Order.created_at.desc()).limit(max(1, min(limit, 500)))
    if status:
        stmt = stmt.where(Order.status == status)
    async with ctx.session() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return {"orders": [ser.order(o) for o in rows]}


@router.post("/orders/{order_id}/refulfill")
async def refulfill(order_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    async with ctx.session() as session, session.begin():
        admin_svc.audit(session, admin_id=admin_id, source=SRC, action="order.refulfill", entity_type="order", entity_id=order_id, reason=body.reason)
    ok = await pay_svc.fulfill_order(ctx, order_id)
    return {"ok": ok}


@router.get("/plans")
async def plans(admin_id: AdminId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        rows = await pay_svc.list_plans(session, only_active=False)
    return {"plans": [ser.plan(p) for p in rows]}


@router.post("/plans")
async def create_plan(body: PlanCreateIn, admin_id: AdminId, ctx: Ctx) -> dict:
    values = body.model_dump(exclude={"reason"})
    plan = await admin_svc.create_plan(ctx, admin_id, SRC, values, body.reason)
    return {"plan": ser.plan(plan)}


@router.patch("/plans/{plan_id}")
async def update_plan(plan_id: int, body: PlanPatchIn, admin_id: AdminId, ctx: Ctx) -> dict:
    plan = await admin_svc.update_plan(ctx, admin_id, SRC, plan_id, body.changes, body.reason)
    return {"plan": ser.plan(plan)}


@router.get("/settings")
async def get_settings(admin_id: AdminId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        values = await settings_svc.get_all(session)
    schema = [
        {"key": k, "label": label, "type": "bool" if typ is bool else "int", "default": default, "bounds": settings_svc.INT_BOUNDS.get(k)}
        for k, (default, typ, label) in settings_svc.SETTINGS_SCHEMA.items()
    ]
    return {"values": values, "schema": schema}


@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingIn, admin_id: AdminId, ctx: Ctx) -> dict:
    value = await admin_svc.update_setting(ctx, admin_id, SRC, key, body.value, body.reason)
    return {"key": key, "value": value}


@router.get("/referrals")
async def referrals(ctx: Ctx, admin_id: int = require_perm("users.view")) -> dict:
    from backend.services import referrals as ref_svc

    async with ctx.session() as session:
        data = await admin_svc.stats(session)
        rewards = await admin_svc.reward_history(session)
        overview, top = await ref_svc.admin_overview(session)
    keys = ("referrals_total", "referrals_with_trial", "referrals_with_payment", "bonus_days_granted")
    return {"stats": {k: data[k] for k in keys}, "rewards": [ser.reward(r) for r in rewards], "overview": overview, "top": top}


@router.get("/referrals/user")
async def referrals_user(ctx: Ctx, q: str = "", admin_id: int = require_perm("users.view")) -> dict:
    from backend.api.miniapp import referral_link
    from backend.services import referrals as ref_svc

    async with ctx.session() as session:
        user = await admin_svc.resolve_user(session, q[:64])
        data = await ref_svc.admin_user(session, user)
    return {"user": {"id": user.id, "name": user.display_name, "username": user.username, "public_id": user.public_id},
            "link": referral_link(ctx, user.referral_code), **data}


@router.get("/referrals/export.csv")
async def referrals_export(ctx: Ctx, admin_id: int = require_perm("users.view")):
    import csv
    import io

    from fastapi.responses import Response

    from backend.services import referrals as ref_svc

    async with ctx.session() as session:
        rows = await ref_svc.export_rows(session)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["referrer_telegram_id", "username", "spiritt_id", "invited", "buyers", "purchases", "paid_rub"])
    w.writerows(rows)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="spiritt_referrals.csv"'})


@router.post("/rewards/{reward_id}/reverse")
async def reverse_reward(reward_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    r = await admin_svc.reverse_reward(ctx, admin_id, SRC, reward_id, body.reason)
    return {"reward": ser.reward(r)}


@router.get("/nodes")
async def nodes(admin_id: AdminId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        rows = await admin_svc.nodes_overview(session)
    return {"nodes": [{**n, "health_at": ser.ts(n["health_at"])} for n in rows]}


@router.post("/nodes/check")
async def nodes_check(admin_id: AdminId, ctx: Ctx) -> dict:
    limiter.check(f"nodes_check:{admin_id}", 6, 60)
    return {"results": await dev_svc.check_nodes(ctx)}


@router.get("/vpn-operations")
async def vpn_operations(admin_id: AdminId, ctx: Ctx, problems: bool = False, limit: int = 100) -> dict:
    async with ctx.session() as session:
        rows = await admin_svc.recent_vpn_ops(session, only_problems=problems, limit=max(1, min(limit, 500)))
    return {"operations": [ser.vpn_op(o) for o in rows]}


@router.get("/audit")
async def audit(admin_id: AdminId, ctx: Ctx, limit: int = 100) -> dict:
    async with ctx.session() as session:
        rows = await admin_svc.recent_audit(session, limit=max(1, min(limit, 500)))
    return {"items": [ser.audit(a) for a in rows]}


@router.get("/integrations")
async def integrations(admin_id: AdminId, ctx: Ctx) -> dict:
    s = ctx.settings
    return {
        "bot": {"configured": bool(s.bot_token.get_secret_value()), "username": ctx.extra.get("bot_username")},
        "webapp_url": s.webapp_url or None,
        "public_base_url": s.public_base_url or None,
        "payments": {
            "provider": ctx.payments.name if ctx.payments else None,
            "webhook_url": f"{s.public_base_url.rstrip('/')}/api/payments/webhook/yookassa" if s.public_base_url else None,
            "verify_ip": s.yookassa_verify_ip,
        },
        "vpn_nodes": [
            {"code": spec.code, "name": spec.name, "api_url_configured": bool(spec.api_url), "token_configured": bool(s.node_token(spec.code))}
            for spec in s.node_specs()
        ],
        "app_env": s.app_env,
    }


# ---------------- manual grants / blocking ----------------

class GrantIn(ReasonIn):
    mode: str = Field(pattern="^(extend|replace|set_end|lifetime|shorten|terminate|revoke)$")
    amount: int | None = Field(default=None, ge=1, le=100000)
    unit: str | None = Field(default=None, pattern="^(hours|days|weeks|months)$")
    plan_code: str | None = Field(default=None, max_length=32)
    ends_at: datetime | None = None
    starts_at: datetime | None = None
    device_limit: int | None = Field(default=None, ge=1, le=50)
    note: str | None = Field(default=None, max_length=200)
    notify: bool = True


@router.post("/users/{user_id}/grant")
async def grant(user_id: int, body: GrantIn, admin_id: AdminId, ctx: Ctx) -> dict:
    limiter.check(f"grant:{admin_id}", 30, 60)
    res = await grants_svc.grant(
        ctx, admin_id, SRC, user_id, mode=body.mode, amount=body.amount, unit=body.unit, plan_code=body.plan_code,
        ends_at=body.ends_at, starts_at=body.starts_at, device_limit=body.device_limit, reason=body.reason, note=body.note, notify=body.notify,
    )
    return {"subscription": ser.subscription(res.subscription), "vpn": res.vpn, "message": res.message}


@router.post("/users/{user_id}/block")
async def block(user_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    user, ok, failed = await admin_svc.set_blocked(ctx, admin_id, SRC, user_id, body.reason, blocked=True)
    return {"user": ser.user(user), "vpn": {"ok": ok, "failed": failed}}


@router.post("/users/{user_id}/unblock")
async def unblock(user_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    user, ok, failed = await admin_svc.set_blocked(ctx, admin_id, SRC, user_id, body.reason, blocked=False)
    return {"user": ser.user(user), "vpn": {"ok": ok, "failed": failed}}


@router.post("/users/{user_id}/support-limit-reset")
async def support_limit_reset(user_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    used = await admin_svc.reset_support_limit(ctx, admin_id, SRC, user_id, body.reason)
    return {"ok": True, "was_used": used}


# ---------------- admin roles ----------------

class AdminAddIn(ReasonIn):
    query: str = Field(min_length=1, max_length=64)
    role: str = Field(default="admin", pattern="^(admin|owner|moderator)$")


def _admin_entry(e: dict) -> dict:
    return {**e, "granted_at": ser.ts(e.get("granted_at"))}


@router.get("/admins")
async def admins(admin_id: AdminId, ctx: Ctx) -> dict:
    return {"admins": [_admin_entry(e) for e in await roles.list_admins(ctx)]}


@router.post("/admins")
async def add_admin(body: AdminAddIn, owner_id: OwnerId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        target = await admin_svc.resolve_user(session, body.query)
    if body.role == "owner":
        entry = await roles.grant_owner(ctx, owner_id, target.id, body.reason, SRC)
    else:
        entry = await roles.grant_admin(ctx, owner_id, target.id, body.reason, SRC, role=body.role)
    return {"admin": _admin_entry(entry)}


@router.post("/admins/{user_id}/revoke")
async def revoke_admin(user_id: int, body: ReasonIn, owner_id: OwnerId, ctx: Ctx) -> dict:
    await roles.revoke_admin(ctx, owner_id, user_id, body.reason, SRC)
    return {"ok": True}


# ---------------- support ----------------

class AdminReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    internal: bool = False
    status: str | None = None


class StatusIn(BaseModel):
    status: str


class AssignIn(BaseModel):
    admin_id: int | None = None


def _support_meta() -> dict:
    return {
        "categories": [{"key": k, "label": v} for k, v in support_svc.CATEGORIES.items()],
        "statuses": [{"key": k, "label": v} for k, v in support_svc.STATUS_LABELS.items()],
    }


@router.get("/support/tickets")
async def support_list(admin_id: StaffId, ctx: Ctx, status: str = "", category: str = "", assigned: str = "") -> dict:
    rows = await support_svc.admin_list(ctx, admin_id, status=status, category=category, assigned=assigned)
    return {"tickets": [ser.ticket(t, p, admin_user=u) for t, u, p in rows], **_support_meta()}


async def _thread_payload(ctx, ticket_id: int) -> dict:
    t, u, msgs = await support_svc.admin_thread(ctx, ticket_id)
    admins_list = [{"user_id": e["user_id"], "name": e["first_name"] or (f"@{e['username']}" if e["username"] else str(e["user_id"]))} for e in await roles.list_admins(ctx)]
    return {"ticket": ser.ticket(t, admin_user=u), "messages": [ser.admin_message(m) for m in msgs], "admins": admins_list}


@router.get("/support/tickets/{ticket_id}")
async def support_thread(ticket_id: int, admin_id: StaffId, ctx: Ctx) -> dict:
    return await _thread_payload(ctx, ticket_id)


@router.post("/support/tickets/{ticket_id}/reply")
async def support_reply(ticket_id: int, body: AdminReplyIn, admin_id: StaffId, ctx: Ctx) -> dict:
    t, m = await support_svc.admin_reply(ctx, admin_id, ticket_id, body.text, internal=body.internal, status=body.status or None)
    async with ctx.session() as session:
        u = await session.get(User, t.user_id)
    return {"message": ser.admin_message(m), "ticket": ser.ticket(t, admin_user=u)}


@router.post("/support/tickets/{ticket_id}/status")
async def support_status(ticket_id: int, body: StatusIn, admin_id: StaffId, ctx: Ctx) -> dict:
    t = await support_svc.set_status(ctx, admin_id, ticket_id, body.status)
    async with ctx.session() as session:
        u = await session.get(User, t.user_id)
    return {"ticket": ser.ticket(t, admin_user=u)}


@router.post("/support/tickets/{ticket_id}/assign")
async def support_assign(ticket_id: int, body: AssignIn, admin_id: StaffId, ctx: Ctx) -> dict:
    t = await support_svc.assign(ctx, admin_id, ticket_id, body.admin_id)
    async with ctx.session() as session:
        u = await session.get(User, t.user_id)
    return {"ticket": ser.ticket(t, admin_user=u)}


# ---------------- promo codes ----------------

class PromoCreateIn(ReasonIn):
    code: str | None = Field(default=None, max_length=32)
    kind: str = Field(pattern="^(percent|fixed|days)$")
    value: int
    max_activations: int | None = Field(default=None, ge=1, le=1_000_000)
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=200)


@router.get("/promos")
async def promos(admin_id: AdminId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    items = await promo_svc.list_promos(ctx)
    async with ctx.session() as session:
        stats = [await promo_svc.promo_stats(session, p) for p in items]
    return {"promos": [promo_svc.promo_json(p, s) for p, s in zip(items, stats)]}


class PromoPatchIn(ReasonIn):
    value: int | None = None
    max_activations: int | None = Field(default=None, ge=1, le=1_000_000)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=200)


@router.patch("/promos/{promo_id}")
async def promo_update(promo_id: int, body: PromoPatchIn, ctx: Ctx, admin_id: int = require_perm("plans.manage")) -> dict:
    from backend.services import promo as promo_svc

    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "reason"}
    p = await promo_svc.update(ctx, admin_id, SRC, promo_id, changes, body.reason)
    async with ctx.session() as session:
        return {"promo": promo_svc.promo_json(p, await promo_svc.promo_stats(session, p))}


@router.get("/promos/{promo_id}/orders")
async def promo_orders(promo_id: int, ctx: Ctx, admin_id: int = require_perm("users.view")) -> dict:
    from backend.services import promo as promo_svc

    return {"orders": await promo_svc.promo_orders(ctx, promo_id)}


@router.post("/promos")
async def promo_create(body: PromoCreateIn, admin_id: AdminId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    p = await promo_svc.create(
        ctx, admin_id, SRC, code=body.code, kind=body.kind, value=body.value, max_activations=body.max_activations,
        expires_at=body.expires_at, note=body.note, reason=body.reason,
    )
    return {"promo": promo_svc.promo_json(p)}


@router.post("/promos/{promo_id}/disable")
async def promo_disable(promo_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    return {"promo": promo_svc.promo_json(await promo_svc.set_active(ctx, admin_id, SRC, promo_id, False, body.reason))}


@router.post("/promos/{promo_id}/enable")
async def promo_enable(promo_id: int, body: ReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    return {"promo": promo_svc.promo_json(await promo_svc.set_active(ctx, admin_id, SRC, promo_id, True, body.reason))}


@router.get("/promos/{promo_id}/activations")
async def promo_activations(promo_id: int, admin_id: AdminId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    rows = await promo_svc.list_activations(ctx, promo_id)
    return {
        "activations": [
            {"id": a.id, "user_id": u.id, "user_public_id": u.public_id, "status": a.status, "order_id": a.order_id, "created_at": ser.ts(a.created_at), "used_at": ser.ts(a.used_at)}
            for a, u in rows
        ]
    }


# ---------------- SpirCoin economy ----------------

class CoinsIn(ReasonIn):
    amount: int = Field(ge=-10_000_000, le=10_000_000)


@router.get("/coins/transactions")
async def coin_transactions(ctx: Ctx, user_id: int | None = None, limit: int = 100, admin_id: int = require_perm("users.view")) -> dict:
    async with ctx.session() as session:
        rows = await coins_svc.recent_transactions(session, user_id=user_id, limit=limit)
    return {"items": [ser.coin_tx(t, user=u) for t, u in rows]}


@router.post("/users/{user_id}/coins")
async def coins_adjust(user_id: int, body: CoinsIn, ctx: Ctx, admin_id: int = require_perm("coins.manage")) -> dict:
    return {"balance": await coins_svc.admin_adjust(ctx, admin_id, SRC, user_id, body.amount, body.reason)}


# ---------------- tasks ----------------

class TaskIn(ReasonIn):
    code: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    reward: int | None = None
    verify: str | None = None
    chat_id: str | None = None
    is_active: bool | None = None
    budget_total: int | None = None
    max_completions: int | None = None
    sort_order: int | None = None


def _task_values(body: TaskIn) -> dict:
    # Only fields the client actually sent; explicit null clears budget/limit/chat.
    return {k: getattr(body, k) for k in body.model_fields_set if k not in ("reason",)}


@router.get("/tasks")
async def tasks_list(ctx: Ctx, admin_id: int = require_perm("tasks.review")) -> dict:
    return {"tasks": await tasks_svc.admin_list(ctx)}


@router.post("/tasks")
async def task_create(body: TaskIn, ctx: Ctx, admin_id: int = require_perm("tasks.manage")) -> dict:
    t = await tasks_svc.create(ctx, admin_id, SRC, _task_values(body), body.reason)
    return {"task": tasks_svc.task_dict(t)}


@router.patch("/tasks/{task_id}")
async def task_update(task_id: int, body: TaskIn, ctx: Ctx, admin_id: int = require_perm("tasks.manage")) -> dict:
    values = _task_values(body)
    values.pop("code", None)
    t = await tasks_svc.update(ctx, admin_id, SRC, task_id, values, body.reason)
    return {"task": tasks_svc.task_dict(t)}


@router.post("/tasks/{task_id}/archive")
async def task_archive(task_id: int, body: ReasonIn, ctx: Ctx, admin_id: int = require_perm("tasks.manage")) -> dict:
    t = await tasks_svc.archive(ctx, admin_id, SRC, task_id, body.reason)
    return {"task": tasks_svc.task_dict(t)}


@router.get("/task-claims")
async def task_claims(ctx: Ctx, status: str = "pending", admin_id: int = require_perm("tasks.review")) -> dict:
    if status not in ("", "pending", "rewarded", "rejected"):
        raise HTTPException(400, "Неизвестный статус")
    return {"items": await tasks_svc.claims_list(ctx, status=status)}


@router.post("/task-claims/{claim_id}/approve")
async def task_claim_approve(claim_id: int, body: ReasonIn, ctx: Ctx, admin_id: int = require_perm("tasks.review")) -> dict:
    c = await tasks_svc.review(ctx, admin_id, SRC, claim_id, approve=True, reason=body.reason)
    return {"id": c.id, "status": c.status}


@router.post("/task-claims/{claim_id}/reject")
async def task_claim_reject(claim_id: int, body: ReasonIn, ctx: Ctx, admin_id: int = require_perm("tasks.review")) -> dict:
    c = await tasks_svc.review(ctx, admin_id, SRC, claim_id, approve=False, reason=body.reason)
    return {"id": c.id, "status": c.status}


# ---------------- trial codes ----------------

class TrialCodeIn(ReasonIn):
    code: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)
    days: int | None = None
    max_activations: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool | None = None


class TrialCodeBulkIn(TrialCodeIn):
    count: int = Field(ge=1, le=200)
    length: int = Field(default=8, ge=6, le=16)
    prefix: str = Field(default="", max_length=12)


def _tc_values(body: TrialCodeIn, *, skip: tuple[str, ...] = ()) -> dict:
    return {k: getattr(body, k) for k in body.model_fields_set if k not in ("reason", "count", "length", "prefix", *skip)}


@router.get("/trial-codes")
async def trial_codes(ctx: Ctx, q: str = "", status: str = "all", sort: str = "new", admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    if status not in ("all", "active", "disabled", "scheduled", "expired", "exhausted", "archived"):
        raise HTTPException(400, "Неизвестный статус")
    items = await tc_svc.admin_list(ctx, q=q[:64], status=status, sort=sort)
    async with ctx.session() as session:
        cfg = {k: await settings_svc.get(session, k) for k in ("trial_code_after_trial", "trial_codes_admin_access")}
    return {"codes": [tc_svc.code_dict(c) for c in items], "settings": cfg}


@router.post("/trial-codes")
async def trial_code_create(body: TrialCodeIn, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    c = await tc_svc.create(ctx, admin_id, SRC, _tc_values(body), body.reason)
    return {"code": tc_svc.code_dict(c)}


@router.post("/trial-codes/bulk")
async def trial_code_bulk(body: TrialCodeBulkIn, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    items = await tc_svc.bulk_create(ctx, admin_id, SRC, count=body.count, length=body.length, prefix=body.prefix,
                                     values=_tc_values(body, skip=("code",)), reason=body.reason)
    return {"codes": [tc_svc.code_dict(c) for c in items]}


@router.patch("/trial-codes/{code_id}")
async def trial_code_update(code_id: int, body: TrialCodeIn, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    c = await tc_svc.update(ctx, admin_id, SRC, code_id, _tc_values(body, skip=("code",)), body.reason)
    return {"code": tc_svc.code_dict(c)}


@router.post("/trial-codes/{code_id}/archive")
async def trial_code_archive(code_id: int, body: ReasonIn, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    return {"code": tc_svc.code_dict(await tc_svc.archive(ctx, admin_id, SRC, code_id, body.reason))}


@router.delete("/trial-codes/{code_id}")
async def trial_code_delete(code_id: int, body: ReasonIn, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    await tc_svc.delete(ctx, admin_id, SRC, code_id, body.reason)
    return {"ok": True}


@router.get("/trial-codes/{code_id}/activations")
async def trial_code_activations(code_id: int, ctx: Ctx, admin_id: int = require_perm("trialcodes.manage")) -> dict:
    from backend.services import trial_codes as tc_svc

    return {"items": await tc_svc.activations(ctx, code_id)}


# ---------------- broadcasts & maintenance ----------------

from backend.services import broadcast as broadcast_svc  # noqa: E402


class OptReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class BroadcastIn(OptReasonIn):
    mode: str = Field(pattern=r"^(single|all)$")
    text: str = Field(min_length=1, max_length=broadcast_svc.MAX_TEXT)
    target_user_id: int | None = None


class MaintenanceAnnounceIn(OptReasonIn):
    text: str = Field(min_length=1, max_length=broadcast_svc.MAX_TEXT)
    auto_restore: bool = True
    restore_text: str | None = Field(default=None, max_length=broadcast_svc.MAX_TEXT)


class MaintenanceRestoreIn(OptReasonIn):
    text: str = Field(min_length=1, max_length=broadcast_svc.MAX_TEXT)


@router.get("/broadcasts")
async def broadcasts_list(admin_id: AdminId, ctx: Ctx, limit: int = 30) -> dict:
    return {"items": await broadcast_svc.list_recent(ctx, max(1, min(limit, 100))), "recipients_all": await broadcast_svc.recipients_count(ctx)}


@router.get("/broadcasts/preview-user/{user_id}")
async def broadcast_preview_user(user_id: int, admin_id: AdminId, ctx: Ctx) -> dict:
    u = await broadcast_svc.find_user(ctx, user_id)
    return {"user": {"id": u.id, "name": u.display_name, "username": u.username, "is_banned": u.is_banned}}


@router.post("/broadcasts")
async def broadcast_create(body: BroadcastIn, admin_id: AdminId, ctx: Ctx) -> dict:
    limiter.check(f"broadcast:{admin_id}", 10, 60)
    if body.mode == "single" and body.target_user_id is None:
        raise HTTPException(400, "Укажите Telegram ID получателя")
    b = await broadcast_svc.create(ctx, admin_id=admin_id, source=SRC, kind=body.mode, text_=body.text, reason=body.reason,
                                   target_user_id=body.target_user_id)
    return broadcast_svc.serialize(b)


@router.post("/broadcasts/{broadcast_id}/cancel")
async def broadcast_cancel(broadcast_id: int, body: OptReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    return broadcast_svc.serialize(await broadcast_svc.cancel(ctx, admin_id, SRC, broadcast_id, body.reason))


@router.get("/maintenance")
async def maintenance_get(admin_id: AdminId, ctx: Ctx) -> dict:
    state = await broadcast_svc.get_maintenance(ctx)
    return {"state": state.dict(), "templates": {"down": broadcast_svc.TEMPLATE_DOWN, "up": broadcast_svc.TEMPLATE_UP},
            "recipients_all": await broadcast_svc.recipients_count(ctx)}


@router.post("/maintenance/announce")
async def maintenance_announce(body: MaintenanceAnnounceIn, admin_id: AdminId, ctx: Ctx) -> dict:
    limiter.check(f"broadcast:{admin_id}", 10, 60)
    state, b = await broadcast_svc.announce_maintenance(ctx, admin_id, SRC, body.text, auto_restore=body.auto_restore,
                                                        restore_text=body.restore_text, reason=body.reason)
    return {"state": state.dict(), "broadcast": broadcast_svc.serialize(b)}


@router.post("/maintenance/restore")
async def maintenance_restore(body: MaintenanceRestoreIn, admin_id: AdminId, ctx: Ctx) -> dict:
    limiter.check(f"broadcast:{admin_id}", 10, 60)
    state, b = await broadcast_svc.restore_now(ctx, admin_id, SRC, body.text, body.reason)
    return {"state": state.dict(), "broadcast": broadcast_svc.serialize(b)}


@router.post("/maintenance/cancel")
async def maintenance_cancel(body: OptReasonIn, admin_id: AdminId, ctx: Ctx) -> dict:
    return {"state": (await broadcast_svc.cancel_maintenance(ctx, admin_id, SRC, body.reason)).dict()}

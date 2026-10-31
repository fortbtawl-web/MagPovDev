"""Mini App API. Identity comes only from validated initData -> short-lived session token."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api import serializers as ser
from backend.api.deps import ActiveUserId, Ctx, GatedUserId, UserId, client_ip, limiter
from backend.services import access
from backend.services import coins as coins_svc
from backend.services import settings as settings_svc
from backend.services import tasks as tasks_svc
from backend.services import roles
from backend.services import support as support_svc
from backend.services import account as account_svc
from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services import referrals as ref_svc
from backend.services import trial as trial_svc
from backend.services.auth import AuthError, issue_token, validate_init_data
from backend.services.context import AppContext, ServiceError
from backend.models import TaskClaim, VpnNode
from sqlalchemy import select

router = APIRouter(prefix="/api")


class InitDataIn(BaseModel):
    init_data: str = Field(min_length=10, max_length=8192)


class OrderIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=32)


class DeviceIn(BaseModel):
    node_code: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=48)


class LabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=48)


class ReplaceIn(BaseModel):
    node_code: str | None = Field(default=None, max_length=32)


class BonusIn(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)


def referral_link(ctx: AppContext, code: str) -> str | None:
    username = ctx.extra.get("bot_username")
    return f"https://t.me/{username}?start=ref_{code}" if username else None


@router.post("/auth/telegram")
async def auth_telegram(body: InitDataIn, request: Request, ctx: Ctx) -> dict:
    limiter.check(f"auth:{client_ip(request)}", 30, 60)
    s = ctx.settings
    try:
        tg = validate_init_data(body.init_data, s.bot_token.get_secret_value(), max_age_seconds=s.initdata_max_age_seconds)
    except AuthError as exc:
        raise HTTPException(401, f"Авторизация Telegram не пройдена: {exc}") from exc
    from urllib.parse import parse_qsl

    start_param = dict(parse_qsl(body.init_data)).get("start_param")
    profile = ref_svc.TgProfile(
        id=tg["id"], username=tg.get("username"), first_name=tg.get("first_name"), last_name=tg.get("last_name"), language_code=tg.get("language_code")
    )
    async with ctx.session() as session, session.begin():
        reg = await ref_svc.register_user(session, profile, start_param)
    secret = s.session_secret.get_secret_value()
    role = await roles.get_role(ctx, tg["id"])
    is_admin = role is not None
    return {
        "token": issue_token(secret, user_id=tg["id"], role="user", ttl_minutes=s.session_ttl_minutes),
        "expires_in": s.session_ttl_minutes * 60,
        "is_admin": is_admin,
        "role": role,
        "admin_token": issue_token(secret, user_id=tg["id"], role="admin", ttl_minutes=s.admin_session_ttl_minutes) if is_admin else None,
        "user": ser.user(reg.user, role),
    }


@router.get("/me")
async def me(uid: UserId, ctx: Ctx) -> dict:
    data = await account_svc.overview(ctx, uid)
    sub = data["subscription"]
    used = sum(1 for d in data["devices"] if d.status in ("pending", "active", "expired", "suspended", "revoking"))
    from backend.models import SupportTicket, TicketStatus, VpnOperation
    from backend.services import subscriptions as subs_svc
    from sqlalchemy import func

    async with ctx.session() as session:
        open_tickets = await session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.user_id == uid, SupportTicket.status.in_(TicketStatus.OPEN))
        )
        dev_ids = [d.id for d in data["devices"]]
        pending_ops = await session.scalar(
            select(func.count(VpnOperation.id)).where(VpnOperation.device_id.in_(dev_ids), VpnOperation.status == "pending")
        ) if dev_ids else 0
    # Entitlement (subscription) and real VPN access are reported separately.
    if sub is None:
        access_state = "none"
    elif not subs_svc.is_usable(sub):
        access_state = "inactive"
    elif pending_ops or any(d.status in ("pending", "revoking") for d in data["devices"]):
        access_state = "pending"
    elif any(d.status == "active" for d in data["devices"]):
        access_state = "ready"
    elif any(d.status in ("expired", "suspended") for d in data["devices"]):
        access_state = "pending"
    else:
        access_state = "no_devices"
    gate = await access.gate_state(ctx, uid)
    # Personal subscription link: prefer the "all locations" key, then any active device.
    live = [d for d in data["devices"] if d.status in ("active", "expired", "suspended") and d.subscription_url]
    live.sort(key=lambda d: (d.node.country_code != "ZZ", d.status != "active", d.id))
    async with ctx.session() as session:
        coins_enabled = bool(await settings_svc.get(session, "coins_enabled"))
    return {
        "coins": data["user"].coin_balance,
        "coins_enabled": coins_enabled,
        "payments_enabled": ctx.payments is not None,
        "subscription_url": live[0].subscription_url if live else None,
        "subscription_location": live[0].node.name if live else None,
        "gate": access.gate_payload(ctx, gate),
        "account_status": "banned" if data["user"].is_banned else "active",
        "access_state": access_state,
        "support_open_tickets": int(open_tickets or 0),
        "user": ser.user(data["user"], await roles.get_role(ctx, uid)),
        "subscription": ser.subscription(sub, used),
        "devices_used": used,
        "devices_active": sum(1 for d in data["devices"] if d.status == "active"),
        "bonus_balance": data["bonus_balance"],
        "trial": {"available": data["trial"].available, "days": data["trial"].days, "reason": data["trial"].reason},
        "trial_code": await _trial_code_state(ctx, uid),
        "support_url": ctx.settings.support_url or None,
        "referral_link": referral_link(ctx, data["user"].referral_code),
    }


@router.get("/plans")
async def plans(ctx: Ctx) -> dict:
    async with ctx.session() as session:
        items = await pay_svc.list_plans(session)
    methods = ["Банковская карта", "СБП", "ЮMoney"] if ctx.payments and ctx.payments.name == "yookassa" else []
    return {
        "plans": [ser.plan(p) for p in items],
        "payments_available": ctx.payments is not None,
        "payments_enabled": ctx.payments is not None,
        "payment_methods": methods,
    }


# ---------------- SpirCoin ----------------

class CoinPurchaseIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(min_length=8, max_length=64)


@router.get("/coins")
async def coins(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        bal = await coins_svc.balance(session, uid)
        items = await coins_svc.history(session, uid, limit=50)
        enabled = bool(await settings_svc.get(session, "coins_enabled"))
    return {"balance": bal, "enabled": enabled, "history": [{k: v for k, v in ser.coin_tx(t).items() if k not in ("actor", "user_id")} for t in items]}


@router.post("/coins/purchase")
async def coins_purchase(body: CoinPurchaseIn, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"coinbuy:{uid}", 10, 60)
    res = await coins_svc.purchase(ctx, uid, body.plan_code, body.idempotency_key)
    from backend.services import subscriptions as subs_svc

    async with ctx.session() as session:
        sub = await subs_svc.get_current(session, uid)
        used = await dev_svc.occupied_slots(session, sub.id) if sub else 0
    return {
        "ok": True, "replayed": res.replayed, "order_id": res.order_id, "balance": res.balance,
        "subscription": ser.subscription(sub, used), "vpn": {"confirmed": res.confirmed, "error": res.error},
    }


# ---------------- tasks ----------------

@router.get("/tasks")
async def tasks(uid: GatedUserId, ctx: Ctx) -> dict:
    return {"tasks": await tasks_svc.list_for_user(ctx, uid)}


@router.post("/tasks/{task_id}/claim")
async def task_claim(task_id: int, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"taskclaim:{uid}", 10, 60)
    res = await tasks_svc.claim(ctx, uid, task_id)
    return {"status": res.status, "message": res.message, "balance": res.balance}


@router.post("/orders")
async def create_order(body: OrderIn, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"order:{uid}", 5, 60)
    order, pay = await pay_svc.create_order(ctx, uid, body.plan_code)
    async with ctx.session() as session:
        from backend.models import Order

        order = await session.get(Order, order.id)
        return ser.order(order)


@router.get("/orders")
async def list_orders(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        return {"orders": [ser.order(o) for o in await pay_svc.user_orders(session, uid)]}


@router.get("/orders/{order_id}")
async def order_status(order_id: int, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"order_status:{uid}", 20, 60)
    order = await pay_svc.refresh_order(ctx, uid, order_id)
    return ser.order(order)


async def _trial_code_state(ctx: AppContext, uid: int) -> dict:
    from backend.services import trial_codes as tc_svc

    st = await tc_svc.info(ctx, uid)
    return {"available": st.available, "reason": st.reason, "used": st.used, "expires_at": ser.ts(st.expires_at)}


class TrialCodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/trial-code")
async def activate_trial_code(body: TrialCodeIn, uid: GatedUserId, ctx: Ctx) -> dict:
    from backend.services import trial_codes as tc_svc

    limiter.check(f"trialcode:{uid}", 6, 60)
    res = await tc_svc.activate(ctx, uid, body.code)
    return {"ok": True, "days": res.days, "expires_at": ser.ts(res.expires_at), "message": res.message, "device": ser.device(res.device, with_links=True)}


@router.post("/trial")
async def activate_trial(uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"trial:{uid}", 3, 60)
    res = await trial_svc.activate_trial(ctx, uid)
    return {"ok": True, "device": ser.device(res.device, with_links=True)}


@router.get("/nodes")
async def nodes(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        rows = (await session.execute(select(VpnNode).where(VpnNode.is_active.is_(True)).order_by(VpnNode.id))).scalars().all()
    return {"nodes": [{"code": n.code, "name": n.name, "flag": n.flag, "online": n.last_health_ok} for n in rows]}


@router.get("/devices")
async def devices(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        items = await dev_svc.list_devices(session, uid)
        from backend.services import subscriptions as subs_svc

        sub = await subs_svc.get_current(session, uid)
    used = sum(1 for d in items if d.status in ("pending", "active", "expired", "suspended", "revoking"))
    return {"devices": [ser.device(d) for d in items], "used": used, "limit": sub.device_limit if sub else 0, "subscription": ser.subscription(sub, used)}


@router.post("/devices")
async def add_device(body: DeviceIn, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"device_add:{uid}", 5, 60)
    res = await dev_svc.add_device(ctx, uid, node_code=body.node_code, label=body.label)
    return {"confirmed": res.confirmed, "error": res.error, "device": ser.device(res.device, with_links=res.confirmed)}


@router.get("/devices/{device_id}/link")
async def device_link(device_id: int, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"link:{uid}", 30, 60)
    device, live = await dev_svc.refresh_link(ctx, uid, device_id)
    return {"live": live, "subscription_url": device.subscription_url, "link": device.connection_link, "device": ser.device(device)}


@router.patch("/devices/{device_id}")
async def rename_device(device_id: int, body: LabelIn, uid: GatedUserId, ctx: Ctx) -> dict:
    device = await dev_svc.rename_device(ctx, uid, device_id, body.label)
    return {"id": device.id, "label": device.label}


@router.post("/devices/{device_id}/replace")
async def replace_device(device_id: int, body: ReplaceIn, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"device_replace:{uid}", 3, 60)
    res = await dev_svc.replace_device(ctx, uid, device_id, node_code=body.node_code)
    return {"confirmed": res.confirmed, "error": res.error, "device": ser.device(res.device, with_links=res.confirmed)}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: int, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"device_revoke:{uid}", 10, 60)
    res = await dev_svc.revoke_device(ctx, device_id, user_id=uid)
    return {
        "confirmed": res.confirmed,
        "status": res.device.status,
        "message": "Устройство отключено" if res.confirmed else "Сервер не подтвердил отключение. Мы повторим автоматически.",
    }


@router.get("/referrals")
async def referrals(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        from backend.models import User

        user = await session.get(User, uid)
        summary = await ref_svc.referral_summary(session, uid)
        invites = await ref_svc.list_invites(session, uid)
        from backend.services import settings as settings_svc

        cfg = await settings_svc.get_all(session)
        stats = await ref_svc.referral_stats(session, uid)
        events = await ref_svc.referral_events(session, uid)
    return {
        "stats": stats,
        "events": [{**e, "at": ser.ts(e["at"])} for e in events],
        "link": referral_link(ctx, user.referral_code),
        "code": user.referral_code,
        "summary": summary,
        "invites": [{**i, "registered_at": ser.ts(i["registered_at"])} for i in invites],
        "rules": {
            "enabled": cfg["referral_enabled"],
            "trial_bonus_days": cfg["referral_trial_bonus_days"] if cfg["referral_trial_bonus_enabled"] else 0,
            "payment_bonus_days": cfg["referral_payment_bonus_days"] if cfg["referral_payment_bonus_enabled"] else 0,
            "trial_days": cfg["trial_days"],
        },
    }


@router.get("/bonus/history")
async def bonus_history(uid: GatedUserId, ctx: Ctx) -> dict:
    async with ctx.session() as session:
        items = await ref_svc.bonus_history(session, uid)
        balance = await ref_svc.bonus_balance(session, uid)
    return {"balance": balance, "items": [ser.bonus(b) for b in items]}


@router.post("/bonus/apply")
async def bonus_apply(body: BonusIn, uid: GatedUserId, ctx: Ctx) -> dict:
    limiter.check(f"bonus:{uid}", 5, 60)
    sub = await account_svc.apply_bonus_days(ctx, uid, body.days)
    return {"subscription": ser.subscription(sub)}


@router.get("/subscription/history")
async def subscription_history(uid: GatedUserId, ctx: Ctx) -> dict:
    from backend.models import SubscriptionEvent

    async with ctx.session() as session:
        rows = (
            await session.execute(
                select(SubscriptionEvent).where(SubscriptionEvent.user_id == uid).order_by(SubscriptionEvent.created_at.desc()).limit(50)
            )
        ).scalars().all()
    return {"items": [ser.sub_event(e) for e in rows]}


# ---------------- support ----------------

class TicketIn(BaseModel):
    category: str = Field(max_length=32)
    text: str = Field(min_length=1, max_length=4000)


class TicketMsgIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _categories() -> list[dict]:
    return [{"key": k, "label": v} for k, v in support_svc.CATEGORIES.items()]


@router.get("/support/tickets")
async def support_tickets(uid: UserId, ctx: Ctx) -> dict:
    rows = await support_svc.user_tickets(ctx, uid)
    return {"tickets": [ser.ticket(t, p) for t, p in rows], "categories": _categories()}


@router.post("/support/tickets")
async def support_create(body: TicketIn, uid: UserId, ctx: Ctx) -> dict:
    limiter.check(f"support_create:{uid}", 3, 60)
    ticket, _ = await support_svc.create_ticket(ctx, uid, body.category, body.text)
    t, msgs = await support_svc.user_thread(ctx, uid, ticket.id)
    return {"ticket": ser.ticket(t), "messages": [ser.user_message(m) for m in msgs]}


@router.get("/support/tickets/{ticket_id}")
async def support_thread(ticket_id: int, uid: UserId, ctx: Ctx) -> dict:
    t, msgs = await support_svc.user_thread(ctx, uid, ticket_id)
    return {"ticket": ser.ticket(t), "messages": [ser.user_message(m) for m in msgs]}


@router.post("/support/tickets/{ticket_id}/messages")
async def support_message(ticket_id: int, body: TicketMsgIn, uid: UserId, ctx: Ctx) -> dict:
    limiter.check(f"support_msg:{uid}", 20, 60)
    t, m = await support_svc.user_message(ctx, uid, ticket_id, body.text)
    return {"message": ser.user_message(m), "ticket": ser.ticket(t)}


@router.post("/support/tickets/{ticket_id}/close")
async def support_close(ticket_id: int, uid: UserId, ctx: Ctx) -> dict:
    t = await support_svc.user_close(ctx, uid, ticket_id)
    return {"ticket": ser.ticket(t)}


# ---------------- access gate ----------------

class TermsIn(BaseModel):
    version: str = Field(min_length=1, max_length=32)


@router.post("/gate/accept-terms")
async def gate_accept_terms(body: TermsIn, uid: UserId, ctx: Ctx) -> dict:
    limiter.check(f"terms:{uid}", 10, 60)
    await access.accept_terms(ctx, uid, body.version, "miniapp")
    return {"gate": access.gate_payload(ctx, await access.gate_state(ctx, uid, force_channel=True))}


@router.post("/gate/check-channel")
async def gate_check_channel(uid: UserId, ctx: Ctx) -> dict:
    limiter.check(f"channel:{uid}", 10, 60)
    return {"gate": access.gate_payload(ctx, await access.gate_state(ctx, uid, force_channel=True))}


# ---------------- promo codes ----------------

class PromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/promo/activate")
async def promo_activate(body: PromoIn, uid: GatedUserId, ctx: Ctx) -> dict:
    # Tight limit: protects against brute-forcing codes.
    limiter.check(f"promo:{uid}", 5, 60)
    from backend.services import promo as promo_svc

    res = await promo_svc.activate(ctx, uid, body.code)
    return {
        "result": {"kind": res.kind, "message": res.message, "days": res.days, "discount_label": res.discount_label},
        "subscription": ser.subscription(res.subscription) if res.subscription else None,
    }


@router.get("/promo/active")
async def promo_active(uid: GatedUserId, ctx: Ctx) -> dict:
    from backend.services import promo as promo_svc

    async with ctx.session() as session:
        found = await promo_svc.active_discount(session, uid)
    if not found:
        return {"discount": None}
    _, p = found
    return {"discount": {"code": p.code, "kind": p.kind, "label": promo_svc.value_label(p.kind, p.value), "expires_at": ser.ts(p.expires_at)}}


# ---------------- task click link (bot buttons) ----------------

@router.get("/go/{task_id}/{user_id}/{sig}", include_in_schema=False)
async def task_click(task_id: int, user_id: int, sig: str, ctx: Ctx):
    """The bot's task button points here: the click is recorded (reward once), then the user goes on to the task URL."""
    import hmac

    from fastapi.responses import HTMLResponse, RedirectResponse

    from backend.models import Task

    secret = ctx.settings.session_secret.get_secret_value()
    if not hmac.compare_digest(sig, tasks_svc.click_sig(secret, task_id, user_id)):
        return HTMLResponse("Ссылка недействительна. Откройте задание в боте ещё раз.", status_code=403)
    limiter.check(f"taskclick:{user_id}", 20, 60)
    async with ctx.session() as session:
        task = await session.get(Task, task_id)
    if task is None or not task.url:
        return HTMLResponse("Задание не найдено.", status_code=404)
    target = RedirectResponse(task.url, status_code=302)
    if task.verify != "telegram" or not task.is_active or task.archived_at is not None:
        return target
    st = await access.gate_state(ctx, user_id)
    if not st.allowed:
        await ctx.notifier.send(user_id, "📢 <b>Награда не начислена</b>\n━━━━━━━━━━━━━━━━\n\nСначала подпишитесь на наш канал и нажмите «Проверить подписку» в боте — после этого задания снова будут приносить SpirCoin.")
        return target
    try:
        async with ctx.session() as session:
            before = await session.scalar(
                select(TaskClaim.status).where(TaskClaim.task_id == task_id, TaskClaim.user_id == user_id)
            )
        res = await tasks_svc.claim(ctx, user_id, task_id)
        if res.status == "rewarded" and before != "rewarded":
            await ctx.notifier.send(user_id, tasks_svc.reward_message(task.reward, res.balance, task.title))
    except ServiceError as exc:
        await ctx.notifier.send(user_id, f"△ {exc.message}")
    return target

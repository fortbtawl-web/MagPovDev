"""Admin Mini App API. Each handler checks a permission; the service layer re-checks it (defence in depth)."""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web
from sqlalchemy import select

from app import texts
from app.api.common import ApiError, json_body, ok, require_perm
from app.api.user import device_json, order_json
from app.models import Order, PromoCode, SupportTicket, User, VPNErrorLog, VPNNode
from app.runtime import Deps, make_notifier
from app.services import admin as svc
from app.services.devices import sync_user_vpn
from app.services.formatting import fmt_money
from app.services.ops import add_admin_reply, check_nodes, close_ticket, open_tickets, ticket_messages
from app.services.roles import ROLE_TITLES
from app.services.settings_store import DEFAULTS, all_settings
from app.services.users import get_user

log = logging.getLogger("spirittvpn.api.admin")
routes = web.RouteTableDef()
A = "/api/v1/admin"


def _tg(request: web.Request) -> int:
    raw = request.match_info["tg"]
    if not raw.isdigit() or len(raw) > 15:
        raise ApiError(400, "bad_id", "Telegram ID — это число.")
    return int(raw)


async def _guard(coro):
    try:
        return await coro
    except svc.AdminInputError as exc:
        raise ApiError(400, "invalid", str(exc)) from None


@routes.get(f"{A}/stats")
async def stats(request: web.Request) -> web.Response:
    admin = require_perm(request, "stats.view")
    deps: Deps = request.app["deps"]
    s = await svc.stats(request["session"], deps.settings, admin, request["now"])
    data = s.__dict__ | {"revenue_text": {p: ", ".join(fmt_money(v, c) for c, v in cur.items()) or "0"
                                          for p, cur in s.revenue.items()},
                         "vpn_provider": deps.vpn.name, "vpn_ready": deps.vpn.is_configured, "vpn_test": deps.vpn.is_test}
    return ok(data)


@routes.get(f"{A}/users/{{tg}}")
async def user_card(request: web.Request) -> web.Response:
    admin = require_perm(request, "users.view")
    deps: Deps = request.app["deps"]
    card = await svc.user_card(request["session"], deps.settings, admin, _tg(request))
    if card is None:
        raise ApiError(404, "not_found", "Пользователь не найден.")
    u, sub = card.user, card.subscription
    return ok({
        "user": {"telegram_id": u.telegram_id, "username": u.username, "first_name": u.first_name, "group": u.group,
                 "blocked": u.is_blocked, "bot_blocked": u.bot_blocked, "created_at": u.created_at,
                 "trial_used": card.trial_used},
        "subscription": None if sub is None else {
            "status": sub.status, "plan": sub.plan_title, "lifetime": sub.is_lifetime, "starts_at": sub.starts_at,
            "expires_at": None if sub.is_lifetime else sub.expires_at, "device_limit": sub.device_limit,
            "vpn_sync_needed": sub.vpn_sync_needed, "vpn_last_error": sub.vpn_last_error},
        "devices": [device_json(d, sub.expires_at if sub else None, bool(sub and sub.is_lifetime)) for d in card.devices],
        "orders": [order_json(o) | {"provider": o.provider} for o in card.orders],
        "promos": [{"code": p.code, "kind": p.kind, "value": p.value, "status": a.status, "created_at": a.created_at}
                   for a, p in card.activations],
        "bonuses": [{"source": b.source, "days": b.days, "description": b.description, "created_at": b.created_at}
                    for b in card.bonuses],
        "referral": card.referral.__dict__,
    })


@routes.post(f"{A}/users/{{tg}}/extend")
async def extend(request: web.Request) -> web.Response:
    admin = require_perm(request, "users.manage")
    deps: Deps = request.app["deps"]
    body = await json_body(request)
    tg = _tg(request)
    session = request["session"]
    sub = await _guard(svc.manual_extend(session, deps.settings, admin, tg, int(body.get("days") or 0),
                                         str(body.get("reason", "")), request["now"],
                                         test_access=bool(body.get("test_access"))))
    user = await get_user(session, tg)
    synced = bool(user) and await sync_user_vpn(session, deps.vpn, user, sub, request["now"])
    await make_notifier(request.app["bot"])(
        tg, f"🎁 Администратор продлил твою подписку SpirittVPN.\n📅 {deps.fmt_until(sub)}", None)
    return ok({"expires_at": None if sub.is_lifetime else sub.expires_at, "vpn_synced": synced})


@routes.post(f"{A}/users/{{tg}}/block")
async def block(request: web.Request) -> web.Response:
    admin = require_perm(request, "users.manage")
    deps: Deps = request.app["deps"]
    body = await json_body(request)
    session = request["session"]
    user = await _guard(svc.set_blocked(session, deps.settings, admin, _tg(request), bool(body.get("blocked")),
                                        request["now"], str(body.get("reason", ""))))
    sub = await svc.get_subscription(session, user.id)
    if sub is not None:
        await sync_user_vpn(session, deps.vpn, user, sub, request["now"])
    return ok({"blocked": user.is_blocked})


@routes.post(f"{A}/devices/{{device_id}}/revoke")
async def revoke_device(request: web.Request) -> web.Response:
    admin = require_perm(request, "devices.manage")
    deps: Deps = request.app["deps"]
    body = await json_body(request)
    await _guard(svc.admin_revoke_device(request["session"], deps.settings, deps.vpn, admin,
                                         request.match_info["device_id"], str(body.get("reason", "")), request["now"]))
    return ok()


@routes.post(f"{A}/orders/{{order_id}}/refund")
async def refund(request: web.Request) -> web.Response:
    admin = require_perm(request, "payments.manage")
    deps: Deps = request.app["deps"]
    body = await json_body(request)
    session = request["session"]
    order = await session.get(Order, request.match_info["order_id"][:36])
    if order is None:
        raise ApiError(404, "not_found", "Заказ не найден.")
    if order.provider == "telegram" and order.currency == "XTR" and order.status == "paid":
        user = await session.get(User, order.user_id)
        try:
            await request.app["bot"].refund_star_payment(user.telegram_id, order.provider_payment_id)
        except TelegramAPIError as exc:
            raise ApiError(502, "refund_failed", f"Telegram отклонил возврат: {type(exc).__name__}") from None
    order = await _guard(svc.admin_refund(session, deps.settings, admin, order.id, str(body.get("reason", "")), request["now"]))
    return ok({"order": order_json(order)})


# ---------- plans ----------

def plan_admin_json(p) -> dict:
    return {"code": p.code, "title": p.title, "description": p.description, "days": p.duration_days,
            "devices": p.device_limit, "lifetime": p.is_lifetime, "price": p.price, "currency": p.currency,
            "price_text": fmt_money(p.price, p.currency), "active": p.is_active, "sort": p.sort_order}


@routes.get(f"{A}/plans")
async def plans(request: web.Request) -> web.Response:
    admin = require_perm(request, "plans.manage")
    items = await svc.list_plans(request["session"], request.app["deps"].settings, admin)
    return ok({"plans": [plan_admin_json(p) for p in items]})


@routes.post(f"{A}/plans")
async def plan_create(request: web.Request) -> web.Response:
    admin = require_perm(request, "plans.manage")
    b = await json_body(request)
    plan = await _guard(svc.create_plan(request["session"], request.app["deps"].settings, admin, str(b.get("code", "")),
                                        b.get("days", 30), b.get("price", 0), str(b.get("title", "")),
                                        b.get("devices", 1), bool(b.get("lifetime"))))
    return ok({"plan": plan_admin_json(plan)}, status=201)


@routes.patch(f"{A}/plans/{{code}}")
async def plan_update(request: web.Request) -> web.Response:
    admin = require_perm(request, "plans.manage")
    b = await json_body(request)
    allowed = {k: v for k, v in b.items() if k in {"price", "days", "devices", "title", "description", "active", "sort"}}
    plan = await _guard(svc.update_plan(request["session"], request.app["deps"].settings, admin,
                                        request.match_info["code"], allowed))
    return ok({"plan": plan_admin_json(plan)})


# ---------- promos ----------

def promo_json(p: PromoCode) -> dict:
    return {"code": p.code, "kind": p.kind, "value": p.value, "description": p.description, "active": p.is_active,
            "archived": p.is_archived, "valid_from": p.valid_from, "valid_until": p.valid_until,
            "max_activations": p.max_activations, "activations": p.activations_count, "group": p.allowed_group,
            "plans": p.allowed_plans, "new_users_only": p.new_users_only}


PROMO_FIELDS = {"value", "description", "active", "valid_from", "valid_until", "max_activations", "group", "plans",
                "new_users_only"}


@routes.get(f"{A}/promos")
async def promos(request: web.Request) -> web.Response:
    admin = require_perm(request, "promos.manage")
    items = await svc.list_promos(request["session"], request.app["deps"].settings, admin,
                                  include_archived=request.query.get("archived") == "1")
    return ok({"promos": [promo_json(p) for p in items]})


@routes.post(f"{A}/promos")
async def promo_create(request: web.Request) -> web.Response:
    admin = require_perm(request, "promos.manage")
    b = await json_body(request)
    fields = {k: v for k, v in b.items() if k in PROMO_FIELDS - {"value"} and v not in (None, "")}
    promo = await _guard(svc.create_promo(request["session"], request.app["deps"].settings, admin, str(b.get("code", "")),
                                          str(b.get("kind", "")), b.get("value", 0), fields))
    return ok({"promo": promo_json(promo)}, status=201)


@routes.patch(f"{A}/promos/{{code}}")
async def promo_update(request: web.Request) -> web.Response:
    admin = require_perm(request, "promos.manage")
    b = await json_body(request)
    fields = {k: ("" if v is None else v) for k, v in b.items() if k in PROMO_FIELDS}
    promo = await _guard(svc.update_promo(request["session"], request.app["deps"].settings, admin,
                                          request.match_info["code"], fields))
    return ok({"promo": promo_json(promo)})


@routes.post(f"{A}/promos/{{code}}/archive")
async def promo_archive(request: web.Request) -> web.Response:
    admin = require_perm(request, "promos.manage")
    promo = await _guard(svc.archive_promo(request["session"], request.app["deps"].settings, admin, request.match_info["code"]))
    return ok({"promo": promo_json(promo)})


# ---------- referrals ----------

@routes.get(f"{A}/referrals")
async def referrals(request: web.Request) -> web.Response:
    admin = require_perm(request, "referrals.manage")
    session = request["session"]
    data, history = await svc.referral_overview(session, request.app["deps"].settings, admin)
    users = {u.id: u for u in (await session.scalars(
        select(User).where(User.id.in_({h.user_id for h in history} | {h.related_user_id for h in history if h.related_user_id}))
    )).all()}
    return ok(data | {"history": [{
        "user": users[h.user_id].telegram_id if h.user_id in users else None,
        "invitee": users[h.related_user_id].telegram_id if h.related_user_id in users else None,
        "source": h.source, "days": h.days, "description": h.description, "created_at": h.created_at,
        "created_by": h.created_by} for h in history]})


@routes.patch(f"{A}/referrals")
async def referral_settings(request: web.Request) -> web.Response:
    admin = require_perm(request, "referrals.manage")
    b = await json_body(request)
    return ok(await _guard(svc.update_referral_settings(request["session"], request.app["deps"].settings, admin, b)))


@routes.post(f"{A}/referrals/correction")
async def referral_correction(request: web.Request) -> web.Response:
    admin = require_perm(request, "referrals.manage")
    b = await json_body(request)
    tg = str(b.get("telegram_id", ""))
    if not tg.isdigit():
        raise ApiError(400, "bad_id", "Telegram ID — это число.")
    entry = await _guard(svc.referral_correction(request["session"], request.app["deps"].settings, admin, int(tg),
                                                 b.get("days", 0), str(b.get("reason", "")), request["now"]))
    return ok({"days": entry.days})


# ---------- nodes ----------

@routes.get(f"{A}/nodes")
async def nodes(request: web.Request) -> web.Response:
    require_perm(request, "nodes.view")
    session = request["session"]
    deps: Deps = request.app["deps"]
    rows = (await session.scalars(select(VPNNode).order_by(VPNNode.name))).all()
    errors = (await session.scalars(select(VPNErrorLog).order_by(VPNErrorLog.id.desc()).limit(20))).all()
    return ok({
        "provider": deps.vpn.name, "configured": deps.vpn.is_configured, "test_mode": deps.vpn.is_test,
        "nodes": [{"id": n.external_id, "name": n.name, "address": n.address, "country": n.country,
                   "online": n.is_connected and not n.is_disabled, "disabled": n.is_disabled, "users_online": n.users_online,
                   "message": n.status_message, "test": n.is_test, "last_checked_at": n.last_checked_at,
                   "last_seen_ok_at": n.last_seen_ok_at} for n in rows],
        "errors": [{"operation": e.operation, "error": e.error, "created_at": e.created_at} for e in errors],
    })


@routes.post(f"{A}/nodes/check")
async def nodes_check(request: web.Request) -> web.Response:
    admin = require_perm(request, "nodes.manage")
    deps: Deps = request.app["deps"]
    total, online = await check_nodes(request["session"], deps.vpn, request["now"])
    await svc.audit(request["session"], admin, "nodes_check", None, total=total, online=online)
    await request["session"].commit()
    return ok({"total": total, "online": online})


# ---------- tickets ----------

@routes.get(f"{A}/tickets")
async def tickets(request: web.Request) -> web.Response:
    require_perm(request, "tickets")
    return ok({"tickets": [{"id": t.id, "category": t.topic, "status": t.status, "created_at": t.created_at,
                            "telegram_id": u.telegram_id, "username": u.username}
                           for t, u in await open_tickets(request["session"])]})


@routes.get(f"{A}/tickets/{{tid}}")
async def ticket(request: web.Request) -> web.Response:
    require_perm(request, "tickets")
    tid = request.match_info["tid"]
    t = await request["session"].get(SupportTicket, int(tid)) if tid.isdigit() else None
    if t is None:
        raise ApiError(404, "not_found", "Обращение не найдено.")
    msgs = await ticket_messages(request["session"], t.id)
    return ok({"id": t.id, "status": t.status, "category": t.topic,
               "messages": [{"from_admin": m.from_admin, "text": m.text, "created_at": m.created_at} for m in msgs]})


@routes.post(f"{A}/tickets/{{tid}}/reply")
async def ticket_reply(request: web.Request) -> web.Response:
    admin = require_perm(request, "tickets")
    b = await json_body(request)
    text = str(b.get("text", "")).strip()
    tid = request.match_info["tid"]
    if not text or not tid.isdigit():
        raise ApiError(400, "invalid", "Пустой ответ.")
    res = await add_admin_reply(request["session"], int(tid), text)
    if res is None:
        raise ApiError(404, "not_found", "Обращение не найдено.")
    t, user = res
    await svc.audit(request["session"], admin, "ticket_reply", str(t.id))
    await request["session"].commit()
    bot: Bot = request.app["bot"]
    delivered = await make_notifier(bot)(user.telegram_id, texts.TICKET_REPLY.format(id=t.id, text=escape(text)), None)
    return ok({"delivered": delivered})


@routes.post(f"{A}/tickets/{{tid}}/close")
async def ticket_close(request: web.Request) -> web.Response:
    admin = require_perm(request, "tickets")
    tid = request.match_info["tid"]
    if not tid.isdigit() or not await close_ticket(request["session"], int(tid)):
        raise ApiError(404, "not_found", "Обращение не найдено.")
    await svc.audit(request["session"], admin, "ticket_close", tid)
    await request["session"].commit()
    return ok()


# ---------- audit / staff / settings ----------

@routes.get(f"{A}/audit")
async def audit(request: web.Request) -> web.Response:
    admin = require_perm(request, "audit.view")
    rows = await svc.recent_audit(request["session"], request.app["deps"].settings, admin, 50)
    return ok({"entries": [{"admin": a.admin_telegram_id, "action": a.action, "target": a.target, "details": a.details,
                            "created_at": a.created_at} for a in rows]})


@routes.get(f"{A}/staff")
async def staff(request: web.Request) -> web.Response:
    admin = require_perm(request, "staff.manage")
    rows = await svc.list_staff(request["session"], request.app["deps"].settings, admin)
    return ok({"staff": [{"telegram_id": tg, "role": role, "title": ROLE_TITLES[role], "source": src} for tg, role, src in rows],
               "roles": {k: v for k, v in ROLE_TITLES.items() if k != "owner"}})


@routes.put(f"{A}/staff/{{tg}}")
async def staff_set(request: web.Request) -> web.Response:
    admin = require_perm(request, "staff.manage")
    b = await json_body(request)
    await _guard(svc.set_staff(request["session"], request.app["deps"].settings, admin, _tg(request), b.get("role") or None))
    return ok()


EDITABLE_SETTINGS = [k for k in DEFAULTS if not k.startswith("referral_")]


@routes.get(f"{A}/settings")
async def settings_get(request: web.Request) -> web.Response:
    require_perm(request, "settings.manage")
    values = await all_settings(request["session"])
    return ok({"settings": {k: values[k] for k in EDITABLE_SETTINGS}})


@routes.patch(f"{A}/settings")
async def settings_set(request: web.Request) -> web.Response:
    admin = require_perm(request, "settings.manage")
    b = await json_body(request)
    for key, value in b.items():
        if key not in EDITABLE_SETTINGS:
            raise ApiError(400, "invalid", f"Неизвестная настройка: {key}")
        raw = ("on" if value else "off") if isinstance(value, bool) else str(value)
        await _guard(svc.change_setting(request["session"], request.app["deps"].settings, admin, key, raw))
    values = await all_settings(request["session"])
    return ok({"settings": {k: values[k] for k in EDITABLE_SETTINGS}})

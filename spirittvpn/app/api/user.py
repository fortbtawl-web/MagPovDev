"""User-facing Mini App API. Identity always comes from validated initData, never from request parameters."""
from __future__ import annotations

import segno
from aiogram import Bot
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from app import branding
from app.api.common import ApiError, json_body, ok
from app.models import ActivationStatus, Device, Order, Plan, User
from app.payments.providers import PaymentUnavailableError
from app.runtime import Deps, notify_admins, notify_referral
from app.services import devices as dev
from app.services import referrals as refs
from app.services import settings_store
from app.services.ops import TICKET_CATEGORIES, create_ticket, user_tickets
from app.services.orders import (
    active_plans,
    create_order,
    mark_failed,
    mark_pending,
    pending_discount,
    quote,
    user_orders,
)
from app.services.promos import activate_promo, describe, user_activations
from app.services.devices import sync_user_vpn
from app.services.formatting import fmt_money
from app.services.roles import ROLE_TITLES, perms_of
from app.services.trial import TrialResult, activate_trial
from app.services.users import accept_terms, account_view, get_acceptance, live_devices, trial_used

routes = web.RouteTableDef()
P = "/api/v1"


def _ctx(request: web.Request) -> tuple[Deps, AsyncSession, User]:
    return request.app["deps"], request["session"], request["user"]


def device_json(d: Device, expires_at, lifetime: bool) -> dict:
    return {"id": d.public_id, "name": d.name, "status": d.status, "created_at": d.created_at,
            "expires_at": None if lifetime else expires_at, "lifetime": lifetime, "client_id": d.external_id,
            "has_error": bool(d.last_error)}


def order_json(o: Order) -> dict:
    return {"id": o.id, "plan": o.plan_title, "status": o.status, "amount": o.amount, "currency": o.currency,
            "amount_text": fmt_money(o.amount, o.currency), "discount": o.discount, "is_test": o.is_test,
            "lifetime": o.is_lifetime, "days": o.duration_days, "devices": o.device_limit,
            "created_at": o.created_at, "paid_at": o.paid_at}


async def account_json(deps: Deps, session: AsyncSession, user: User, role: str | None, now) -> dict:
    view = await account_view(session, user, now)
    sub = view.subscription
    acc = await get_acceptance(session, user, deps.settings.legal_docs_version)
    trial_enabled = await settings_store.get_bool(session, "trial_enabled")
    summary = await refs.summary(session, user)
    return {
        "user": {"telegram_id": user.telegram_id, "first_name": user.first_name, "username": user.username,
                 "blocked": user.is_blocked},
        "subscription": {
            "status": view.status,
            "plan": sub.plan_title if sub else None,
            "is_trial": bool(sub and sub.is_trial),
            "lifetime": view.is_lifetime,
            "starts_at": sub.starts_at if sub else None,
            "expires_at": sub.expires_at if sub and not sub.is_lifetime else None,
            "remaining_seconds": int(view.remaining.total_seconds()) if view.remaining else None,
            "device_limit": view.device_limit,
            "devices_used": view.devices_used,
        },
        "trial": {"used": view.trial_used,
                  "available": not view.trial_used and sub is None and trial_enabled and deps.vpn.is_configured,
                  "days": await settings_store.get_int(session, "trial_days")},
        "vpn": {"ready": deps.vpn.is_configured, "test_mode": deps.vpn.is_test, "provider": deps.vpn.name},
        "payments": {"enabled": deps.payments.is_enabled, "test_mode": deps.payments.is_test},
        "maintenance": await settings_store.get_bool(session, "maintenance"),
        "terms": {"version": deps.settings.legal_docs_version, "accepted_at": acc.accepted_at if acc else None},
        "settings": {"notifications": user.notifications_enabled, "sounds": user.sounds_enabled},
        "referral": {"invited": summary.invited, "bonus_days": summary.bonus_days, "pending": summary.pending,
                     "enabled": await settings_store.get_bool(session, "referral_enabled")},
        "staff": {"role": role, "title": ROLE_TITLES.get(role or ""), "perms": sorted(perms_of(role))} if role else None,
        "links": {
            "privacy": branding.PRIVACY_POLICY_URL, "offer": branding.USER_AGREEMENT_URL,
            "owner": branding.OWNER_USERNAME, "developer": branding.DEVELOPER_USERNAME,
            "support": list(branding.SUPPORT_USERNAMES),
            "support_extra": await settings_store.get_setting(session, "support_extra_url") or None,
        },
    }


@routes.get(f"{P}/me")
async def me(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    return ok(await account_json(deps, session, user, request["role"], request["now"]))


@routes.patch(f"{P}/me/settings")
async def update_settings(request: web.Request) -> web.Response:
    _, session, user = _ctx(request)
    body = await json_body(request)
    if "notifications" in body:
        user.notifications_enabled = bool(body["notifications"])
    if "sounds" in body:
        user.sounds_enabled = bool(body["sounds"])
    await session.commit()
    return ok({"notifications": user.notifications_enabled, "sounds": user.sounds_enabled})


@routes.post(f"{P}/terms/accept")
async def terms_accept(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    body = await json_body(request)
    if body.get("version") != deps.settings.legal_docs_version:
        raise ApiError(409, "terms_outdated", "Документы обновились — открой их ещё раз.")
    await accept_terms(session, user, deps.settings.legal_docs_version, request["now"])
    return ok()


def plan_json(p: Plan, q) -> dict:
    return {"code": p.code, "title": p.title, "description": p.description, "days": p.duration_days,
            "lifetime": p.is_lifetime, "devices": p.device_limit, "price": q.original, "discount": q.discount,
            "total": q.total, "currency": p.currency, "price_text": fmt_money(q.original, p.currency),
            "total_text": fmt_money(q.total, p.currency), "promo": q.promo_code if q.discount else None}


@routes.get(f"{P}/plans")
async def plans(request: web.Request) -> web.Response:
    _, session, user = _ctx(request)
    return ok({"plans": [plan_json(p, await quote(session, user, p)) for p in await active_plans(session)]})


@routes.post(f"{P}/trial")
async def trial(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    out = await activate_trial(session, user, deps.vpn, deps.settings.legal_docs_version, request["now"],
                               device_limit=deps.settings.trial_device_limit, prefix=deps.settings.vpn_username_prefix)
    if out.result != TrialResult.ACTIVATED:
        from app import texts

        status = {TrialResult.NEED_TERMS: 409, TrialResult.VPN_UNAVAILABLE: 503}.get(out.result, 400)
        message = "Нужно принять условия сервиса." if out.result == TrialResult.NEED_TERMS else texts.TRIAL_ERRORS.get(out.result.value, "")
        raise ApiError(status, out.result.value, _plain(message))
    if out.reward:
        await notify_referral(request.app["bot"], out.reward)
    return ok({"expires_at": out.subscription.expires_at if out.subscription else None})


def _plain(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html)


@routes.post(f"{P}/promo")
async def promo(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    if not request.app["rate_limiter"].hit(user.telegram_id, "promo", 10, 600):
        raise ApiError(429, "rate_limited", "Слишком много попыток. Попробуй через 10 минут.")
    body = await json_body(request)
    res = await activate_promo(session, user, str(body.get("code", ""))[:64], request["now"], vpn_ready=deps.vpn.is_configured)
    if not res.ok:
        assert res.error is not None
        raise ApiError(400, res.error.name.lower(), res.error.value)
    assert res.promo is not None
    if res.subscription is not None:
        await sync_user_vpn(session, deps.vpn, user, res.subscription, request["now"])
    return ok({"description": describe(res.promo, lambda v: fmt_money(v, deps.settings.payment_currency)),
               "kind": res.promo.kind, "days_added": res.promo.value if res.subscription is not None else 0})


@routes.post(f"{P}/orders")
async def create(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    body = await json_body(request)
    if not deps.vpn.is_configured:
        raise ApiError(409, "service_not_ready", "VPN-серверы готовятся к запуску — оплата пока не принимается.")
    if not deps.payments.is_enabled:
        raise ApiError(409, "payments_disabled", "Онлайн-оплата временно недоступна.")
    if await get_acceptance(session, user, deps.settings.legal_docs_version) is None:
        raise ApiError(409, "need_terms", "Нужно принять условия сервиса.")
    # Price, term and device limit come only from the DB plan; the client sends nothing but the plan code.
    order = await create_order(session, user, str(body.get("plan_code", ""))[:32], deps.payments.name,
                               deps.payments.is_test, request["now"])
    if order is None:
        raise ApiError(404, "plan_unavailable", "Тариф недоступен.")
    try:
        ps = await deps.payments.create_payment(order)
        url = ps.url
        if ps.kind == "invoice" and ps.invoice:
            bot: Bot = request.app["bot"]
            url = await bot.create_invoice_link(**ps.invoice)
    except PaymentUnavailableError:
        await mark_failed(session, order.id, "payments disabled")
        raise ApiError(409, "payments_disabled", "Онлайн-оплата временно недоступна.") from None
    except Exception:
        await mark_failed(session, order.id, "payment session error")
        raise ApiError(502, "payment_error", "Не удалось создать платёж. Попробуй чуть позже.") from None
    await mark_pending(session, order)
    return ok({"order": order_json(order), "kind": ps.kind, "url": url}, status=201)


@routes.get(f"{P}/orders")
async def orders(request: web.Request) -> web.Response:
    _, session, user = _ctx(request)
    return ok({"orders": [order_json(o) for o in await user_orders(session, user.id, 30)]})


@routes.get(f"{P}/orders/{{order_id}}")
async def order(request: web.Request) -> web.Response:
    _, session, user = _ctx(request)
    o = await session.get(Order, request.match_info["order_id"][:36])
    if o is None or o.user_id != user.id:
        raise ApiError(404, "not_found", "Заказ не найден.")
    await session.refresh(o)
    return ok({"order": order_json(o)})


async def _devices_payload(deps: Deps, session: AsyncSession, user: User, now) -> dict:
    view = await account_view(session, user, now)
    sub = view.subscription
    items = await live_devices(session, user.id)
    return {
        "devices": [device_json(d, sub.expires_at if sub else None, view.is_lifetime) for d in items],
        "used": view.devices_used, "limit": view.device_limit,
        "can_add": deps.vpn.is_configured and view.status in ("active", "soon", "pending") and view.devices_used < view.device_limit,
        "vpn_ready": deps.vpn.is_configured, "test_mode": deps.vpn.is_test,
    }


@routes.get(f"{P}/devices")
async def devices(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    return ok(await _devices_payload(deps, session, user, request["now"]))


def _device_error(err: dev.DeviceError) -> ApiError:
    status = {dev.DeviceError.NOT_FOUND: 404, dev.DeviceError.VPN_UNAVAILABLE: 503, dev.DeviceError.INVALID_NAME: 400}
    return ApiError(status.get(err, 409), err.name.lower(), err.value)


@routes.post(f"{P}/devices")
async def add_device(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    body = await json_body(request)
    res = await dev.add_device(session, deps.vpn, user, str(body.get("name", "")), request["now"],
                               deps.settings.vpn_username_prefix)
    if not res.ok:
        raise _device_error(res.error)  # type: ignore[arg-type]
    return ok(await _devices_payload(deps, session, user, request["now"]), status=201)


@routes.patch(f"{P}/devices/{{device_id}}")
async def rename_device(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    body = await json_body(request)
    res = await dev.rename_device(session, user, request.match_info["device_id"], str(body.get("name", "")))
    if not res.ok:
        raise _device_error(res.error)  # type: ignore[arg-type]
    return ok(await _devices_payload(deps, session, user, request["now"]))


@routes.delete(f"{P}/devices/{{device_id}}")
async def revoke_device(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    res = await dev.revoke_device(session, deps.vpn, user, request.match_info["device_id"], request["now"])
    if not res.ok:
        raise _device_error(res.error)  # type: ignore[arg-type]
    return ok(await _devices_payload(deps, session, user, request["now"]))


@routes.post(f"{P}/devices/{{device_id}}/replace")
async def replace_device(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    body = await json_body(request)
    res = await dev.replace_device(session, deps.vpn, user, request.match_info["device_id"], body.get("name") or None,
                                   request["now"], deps.settings.vpn_username_prefix)
    if not res.ok:
        raise _device_error(res.error)  # type: ignore[arg-type]
    return ok(await _devices_payload(deps, session, user, request["now"]))


@routes.get(f"{P}/devices/{{device_id}}/connection")
async def connection(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    res = await dev.device_connection(session, deps.vpn, user, request.match_info["device_id"])
    if not res.ok or res.connection is None:
        raise _device_error(res.error)  # type: ignore[arg-type]
    link = res.connection.link
    qr = segno.make(link, error="m").svg_inline(scale=5, border=2, dark="#0b0b14", light="#ffffff")
    resp = ok({"link": link, "qr_svg": qr, "is_test": res.connection.is_test})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@routes.get(f"{P}/referrals")
async def referrals(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    link = refs.referral_link(deps.bot_username, user.telegram_id)
    s = await refs.summary(session, user)
    return ok({
        "link": link, "share_url": refs.share_url(link) if link else None,
        "invited": s.invited, "bonus_days": s.bonus_days, "pending": s.pending,
        "enabled": await settings_store.get_bool(session, "referral_enabled"),
        "trial_days": await settings_store.get_int(session, "referral_trial_days"),
        "payment_days": await settings_store.get_int(session, "referral_payment_days"),
        "invites": [{"name": i.name, "invited_at": i.invited_at, "trial_done": i.trial_done,
                     "payment_done": i.payment_done, "bonus_days": i.bonus_days} for i in await refs.invites(session, user)],
    })


@routes.get(f"{P}/bonuses")
async def bonuses(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    money = lambda v: fmt_money(v, deps.settings.payment_currency)  # noqa: E731
    pd = await pending_discount(session, user)
    return ok({
        "entries": [{"source": e.source, "days": e.days, "description": e.description, "created_at": e.created_at}
                    for e in await refs.bonus_history(session, user.id)],
        "promos": [{"code": p.code, "description": describe(p, money), "status": a.status, "created_at": a.created_at}
                   for a, p in await user_activations(session, user.id)],
        "pending_discount": {"code": pd[1].code, "description": describe(pd[1], money)} if pd else None,
        "pending_status": ActivationStatus.PENDING,
    })


@routes.get(f"{P}/support")
async def support_list(request: web.Request) -> web.Response:
    _, session, user = _ctx(request)
    return ok({"categories": TICKET_CATEGORIES, "tickets": [
        {"id": t.id, "category": t.topic, "status": t.status, "created_at": t.created_at,
         "messages": [{"from_admin": m.from_admin, "text": m.text, "created_at": m.created_at} for m in msgs]}
        for t, msgs in await user_tickets(session, user.id)]})


@routes.post(f"{P}/support")
async def support_create(request: web.Request) -> web.Response:
    deps, session, user = _ctx(request)
    if not request.app["rate_limiter"].hit(user.telegram_id, "ticket", 5, 3600):
        raise ApiError(429, "rate_limited", "Слишком много обращений. Попробуй позже.")
    body = await json_body(request)
    text = str(body.get("text", "")).strip()
    if not 5 <= len(text) <= 2000:
        raise ApiError(400, "invalid_text", "Сообщение: от 5 до 2000 символов.")
    tg_id = user.telegram_id
    ticket = await create_ticket(session, user, str(body.get("category", "general")), text)
    from html import escape

    await notify_admins(request.app["bot"], deps.settings,
                        f"🎫 <b>Обращение #{ticket.id}</b> · {TICKET_CATEGORIES.get(ticket.topic)}\nОт: <code>{tg_id}</code>\n\n"
                        f"{escape(text[:3000])}\n\nОтветить: <code>/reply {ticket.id} текст</code> или в админ-панели")
    return ok({"id": ticket.id}, status=201)


__all__ = ["routes", "account_json", "trial_used"]

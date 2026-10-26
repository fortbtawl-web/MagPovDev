"""HTTP server: health check and payment webhooks (+ test checkout page in development)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from aiogram import Bot
from aiohttp import web
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.models import Order
from app.payments.providers import InvalidWebhookError, TestPaymentProvider
from app.runtime import Deps, deliver_payment_result
from app.services.formatting import fmt_money
from app.services.orders import process_payment_event

log = logging.getLogger("spirittvpn.web")
MAX_BODY = 64 * 1024


async def _handle_event(request: web.Request, body: bytes, headers) -> web.Response:
    deps: Deps = request.app["deps"]
    bot: Bot = request.app["bot"]
    try:
        event = deps.payments.parse_webhook(headers, body)
    except InvalidWebhookError as exc:
        log.warning("rejected payment webhook from %s: %s", request.remote, exc)
        return web.json_response({"ok": False}, status=401)
    try:
        async with deps.session_factory() as session:
            result = await process_payment_event(session, event, datetime.now(UTC))
            try:
                await deliver_payment_result(bot, session, deps, result)
            except Exception:  # noqa: BLE001 - payment is committed; notification failures must not trigger provider retries
                log.exception("payment notification failed")
    except (OperationalError, DBAPIError):
        log.exception("db unavailable while processing webhook")
        return web.json_response({"ok": False}, status=503)  # provider will retry
    return web.json_response({"ok": True, "result": result.outcome.value})


async def payment_webhook(request: web.Request) -> web.Response:
    deps: Deps = request.app["deps"]
    if request.match_info["provider"] != deps.payments.name:
        raise web.HTTPNotFound()
    if (request.content_length or 0) > MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length or 0)
    return await _handle_event(request, await request.read(), request.headers)


async def health(request: web.Request) -> web.Response:
    deps: Deps = request.app["deps"]
    try:
        async with deps.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return web.json_response({"status": "db_unavailable"}, status=503)
    return web.json_response({"status": "ok", "vpn": deps.vpn.name, "payments": deps.payments.name})


TEST_PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SpirittVPN — ТЕСТОВАЯ оплата</title>
<body style="background:#07071a;color:#e8e8ff;font-family:system-ui;max-width:480px;margin:40px auto;padding:16px">
<h2 style="color:#7c5cff">🧪 ТЕСТОВАЯ ОПЛАТА</h2>
<p style="background:#2a1a4a;padding:12px;border-radius:8px">Это симуляция для разработки. Реальные деньги не списываются.</p>
<p>Заказ: <code>{order_id}</code><br>Тариф: {plan}<br>Сумма: {amount}</p>
<form method="post" action="/payments/test/checkout/{order_id}/success?token={token}"><button style="padding:12px 20px;background:#2f6bff;color:#fff;border:0;border-radius:8px">Симулировать успешную оплату</button></form><br>
<form method="post" action="/payments/test/checkout/{order_id}/fail?token={token}"><button style="padding:12px 20px;background:#444;color:#fff;border:0;border-radius:8px">Симулировать отказ</button></form>
</body></html>"""


async def test_checkout_page(request: web.Request) -> web.Response:
    deps: Deps = request.app["deps"]
    provider = deps.payments
    assert isinstance(provider, TestPaymentProvider)
    order_id = request.match_info["order_id"]
    token = request.query.get("token", "")
    if token != provider.checkout_token(order_id):
        raise web.HTTPForbidden()
    async with deps.session_factory() as session:
        order = await session.get(Order, order_id)
    if order is None:
        raise web.HTTPNotFound()
    html = TEST_PAGE.format(order_id=escape(order.id), plan=escape(order.plan_title),
                            amount=escape(fmt_money(order.amount, order.currency)), token=escape(token))
    return web.Response(text=html, content_type="text/html")


async def test_checkout_submit(request: web.Request) -> web.Response:
    deps: Deps = request.app["deps"]
    provider = deps.payments
    assert isinstance(provider, TestPaymentProvider)
    order_id = request.match_info["order_id"]
    if request.query.get("token", "") != provider.checkout_token(order_id):
        raise web.HTTPForbidden()
    async with deps.session_factory() as session:
        order = await session.get(Order, order_id)
    if order is None:
        raise web.HTTPNotFound()
    body, headers = provider.build_event(order.id, request.match_info["result"] == "success", order.amount, order.currency)
    resp = await _handle_event(request, body, headers)
    return web.Response(text=f"<meta charset='utf-8'><body style='background:#07071a;color:#e8e8ff;font-family:system-ui'>"
                             f"<h3>🧪 Тестовое событие отправлено ({resp.status}). Вернись в Telegram.</h3>",
                        content_type="text/html")


def _webapp_routes(app: web.Application, dist: Path) -> None:
    """Serves the built Mini App (Vite output) under /app/ with SPA fallback."""
    index = dist / "index.html"

    async def serve(request: web.Request) -> web.StreamResponse:
        rel = request.match_info.get("path", "")
        target = (dist / rel).resolve()
        if rel and target.is_file() and dist.resolve() in target.parents:
            resp = web.FileResponse(target)
            if "/assets/" in f"/{rel}":
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        if not index.is_file():
            return web.Response(status=503, text="Mini App is not built. Run: cd webapp && npm ci && npm run build")
        resp = web.FileResponse(index)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    async def to_app(request: web.Request) -> web.StreamResponse:
        raise web.HTTPFound("/app/")

    app.router.add_get("/app", to_app)
    app.router.add_get("/app/", serve)
    app.router.add_get("/app/{path:.*}", serve)


@web.middleware
async def security_headers(request: web.Request, handler) -> web.StreamResponse:
    resp = await handler(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def build_web_app(deps: Deps, bot: Bot) -> web.Application:
    from app.api import admin as admin_api
    from app.api import user as user_api
    from app.api.common import RateLimiter, api_middleware

    app = web.Application(client_max_size=MAX_BODY, middlewares=[security_headers, api_middleware])
    app["deps"], app["bot"], app["rate_limiter"] = deps, bot, RateLimiter()
    app.router.add_get("/health", health)
    app.router.add_routes(user_api.routes)
    app.router.add_routes(admin_api.routes)
    _webapp_routes(app, Path(deps.settings.webapp_dist))
    app.router.add_post("/payments/{provider}/webhook", payment_webhook)
    if isinstance(deps.payments, TestPaymentProvider) and deps.settings.app_env != "production":
        app.router.add_get("/payments/test/checkout/{order_id}", test_checkout_page)
        app.router.add_post("/payments/test/checkout/{order_id}/{result:success|fail}", test_checkout_submit)
    return app

"""FastAPI application: Mini App API, admin API, payment webhooks, health, static frontend."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.api import admin as admin_api
from backend.api import miniapp
from backend.api.deps import client_ip
from backend.bootstrap import build_context, close_context, setup_logging
from backend.config import get_settings
from backend.integrations.payments import PaymentError, WebhookRejected
from backend.services import devices as dev_svc
from backend.services import payments as pay_svc
from backend.services.context import AppContext, ServiceError
from backend.tasks import worker

log = logging.getLogger("spiritt.api")


def create_app(ctx: AppContext | None = None, *, run_worker: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal ctx
        own = ctx is None
        if own:
            settings = get_settings()
            setup_logging(settings)
            if settings.app_env == "production" and len(settings.session_secret.get_secret_value()) < 32:
                raise RuntimeError("SESSION_SECRET должен быть не короче 32 символов")
            ctx = build_context(settings)
        app.state.ctx = ctx
        from backend.config import missing_required

        problems = missing_required(ctx.settings, "api")
        if problems and ctx.settings.app_env == "production":
            raise RuntimeError("Не заданы обязательные переменные: " + "; ".join(problems))
        await dev_svc.ensure_nodes(ctx)
        bot = getattr(ctx.notifier, "bot", None)
        if bot is not None and "bot_username" not in ctx.extra:
            try:
                me = await bot.get_me()
                ctx.extra["bot_username"] = me.username
            except Exception as exc:  # noqa: BLE001
                log.error("getMe failed — referral links unavailable: %s", type(exc).__name__)
        stop = asyncio.Event()
        task = None
        if (ctx.settings.run_worker if run_worker is None else run_worker):
            task = asyncio.create_task(worker.run_forever(ctx, stop))
        try:
            yield
        finally:
            stop.set()
            if task:
                await task
            if own:
                await close_context(ctx)

    app = FastAPI(title="SpirittVPN API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if ctx is not None:
        app.state.ctx = ctx

    settings = ctx.settings if ctx else get_settings()
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["authorization", "content-type"]
        )

    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=400)

    from backend.services.access import AccessDenied

    @app.exception_handler(AccessDenied)
    async def _access_denied(request: Request, exc: AccessDenied):
        return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=403)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Внутренняя ошибка сервера. Попробуйте позже."}, status_code=500)

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        c: AppContext = request.app.state.ctx
        try:
            async with c.session() as session:
                await session.execute(text("SELECT 1"))
            db = True
        except Exception:  # noqa: BLE001
            db = False
        return JSONResponse({"ok": db, "db": db}, status_code=200 if db else 503)

    @app.post("/api/payments/webhook/yookassa")
    async def yookassa_webhook(request: Request) -> dict:
        c: AppContext = request.app.state.ctx
        body = await request.body()
        try:
            result = await pay_svc.handle_webhook(c, body=body, headers=dict(request.headers), client_ip=client_ip(request))
        except WebhookRejected as exc:
            log.warning("webhook rejected: %s", exc)
            raise HTTPException(403, "rejected") from exc
        except PaymentError as exc:
            # Non-200 makes YooKassa redeliver later.
            log.warning("webhook processing deferred: %s", exc)
            raise HTTPException(503, "retry later") from exc
        return {"ok": True, "result": result}

    app.include_router(miniapp.router)
    app.include_router(admin_api.router)

    dist = Path(settings.frontend_dist)
    if settings.serve_frontend and dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        site = Path("website")
        if site.is_dir():
            app.mount("/site", StaticFiles(directory=site, html=True), name="site")

        @app.get("/admin")
        async def admin_page():
            return FileResponse(dist / "admin.html")

        @app.get("/")
        async def index_page():
            return FileResponse(dist / "index.html")

    return app


app = create_app()

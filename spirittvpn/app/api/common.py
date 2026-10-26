"""Mini App API plumbing: Telegram initData auth, rate limiting, JSON errors, serializers."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram.utils.web_app import WebAppInitData, safe_parse_webapp_init_data
from aiohttp import web
from sqlalchemy.exc import DBAPIError, OperationalError

from app.runtime import Deps
from app.services import settings_store
from app.services.referrals import parse_ref_payload
from app.services.roles import AdminPermissionError, get_role, perms_of
from app.services.users import get_user, register_user

log = logging.getLogger("spirittvpn.api")
API_PREFIX = "/api/v1/"
MAX_BODY = 32 * 1024

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def ok(data: Any = None, status: int = 200) -> web.Response:
    return web.json_response(data if data is not None else {"ok": True}, status=status, dumps=_dumps)


def _dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=_default)


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(type(o).__name__)


def validate_init_data(init_data: str, bot_token: str, max_age: int, now: float | None = None) -> WebAppInitData:
    """Official Telegram scheme (HMAC-SHA256 with key HMAC("WebAppData", bot_token)) + freshness check."""
    try:
        data = safe_parse_webapp_init_data(bot_token, init_data)
    except ValueError as exc:
        raise ApiError(401, "invalid_init_data", "Сессия Telegram не подтверждена. Открой приложение из бота.") from exc
    now = now if now is not None else time.time()
    age = now - data.auth_date.timestamp()
    if age > max_age or age < -60:
        raise ApiError(401, "init_data_expired", "Сессия устарела. Закрой и снова открой приложение.")
    if data.user is None:
        raise ApiError(401, "invalid_init_data", "Нет данных пользователя.")
    return data


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[int, str], deque[float]] = defaultdict(deque)

    def hit(self, key: int, bucket: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        q = self._hits[(key, bucket)]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 100_000:
            self._hits.clear()
        return True


async def json_body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except ValueError:
        raise ApiError(400, "bad_json", "Некорректный запрос.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "Некорректный запрос.")
    return data


def require_perm(request: web.Request, perm: str) -> int:
    """Fast path check from the resolved role; services re-check with require() on every action."""
    if perm not in perms_of(request["role"]):
        raise ApiError(403, "forbidden", "Недостаточно прав.")
    return request["tg_id"]


@web.middleware
async def api_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    if not request.path.startswith(API_PREFIX):
        return await handler(request)
    deps: Deps = request.app["deps"]
    limiter: RateLimiter = request.app["rate_limiter"]
    try:
        if (request.content_length or 0) > MAX_BODY:
            raise ApiError(413, "too_large", "Слишком большой запрос.")
        header = request.headers.get("Authorization", "")
        if not header.startswith("tma "):
            raise ApiError(401, "no_auth", "Открой приложение из Telegram-бота.")
        init = validate_init_data(header[4:], deps.settings.bot_token.get_secret_value(),
                                  deps.settings.init_data_ttl_seconds)
        tg = init.user
        assert tg is not None
        if not limiter.hit(tg.id, "api", deps.settings.api_rate_per_minute, 60):
            raise ApiError(429, "rate_limited", "Слишком много запросов. Подожди немного.")
        async with deps.session_factory() as session:
            now = datetime.now(UTC)
            user = await get_user(session, tg.id)
            if user is None:
                user, _ = await register_user(session, tg.id, tg.username, tg.first_name, now,
                                              parse_ref_payload(init.start_param))
            role = await get_role(session, deps.settings, tg.id)
            request["session"], request["user"], request["role"], request["tg_id"] = session, user, role, tg.id
            request["now"] = now
            if role is None and request.method != "GET":
                if user.is_blocked:
                    raise ApiError(403, "blocked", "Доступ к сервису ограничен. Напиши в поддержку.")
                if await settings_store.get_bool(session, "maintenance"):
                    raise ApiError(503, "maintenance", await settings_store.get_setting(session, "maintenance_text"))
            return await handler(request)
    except ApiError as exc:
        return web.json_response({"error": exc.code, "message": exc.message}, status=exc.status)
    except AdminPermissionError:
        return web.json_response({"error": "forbidden", "message": "Недостаточно прав."}, status=403)
    except (OperationalError, DBAPIError):
        log.exception("database error in API")
        return web.json_response({"error": "db_unavailable", "message": "Сервис временно недоступен."}, status=503)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("unhandled API error")
        return web.json_response({"error": "internal", "message": "Что-то пошло не так. Попробуй ещё раз."}, status=500)

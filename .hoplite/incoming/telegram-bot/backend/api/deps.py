from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from sqlalchemy import select

from backend.services import roles
from backend.services.auth import AuthError, decode_token
from backend.services.context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


class RateLimiter:
    """Sliding-window limiter, in-process. Run one API process or put a proxy limiter in front for more."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Слишком много запросов. Подождите немного.")
        q.append(now)


limiter = RateLimiter()


def client_ip(request: Request) -> str | None:
    ctx: AppContext = request.app.state.ctx
    if ctx.settings.trust_forwarded_for:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else None


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Требуется авторизация")
    return authorization[7:].strip()


async def current_user_id(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> int:
    try:
        uid, role = decode_token(ctx.settings.session_secret.get_secret_value(), _bearer(authorization))
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    if role not in ("user", "admin"):
        raise HTTPException(403, "Недостаточно прав")
    return uid


async def active_user_id(ctx: Ctx, uid: int = Depends(current_user_id)) -> int:
    """Rejects blocked accounts (support and read-only screens stay available via UserId)."""
    from backend.models import User

    async with ctx.session() as session:
        banned = await session.scalar(select(User.is_banned).where(User.id == uid))
    if banned:
        raise HTTPException(403, "Аккаунт заблокирован. Обратитесь в поддержку.")
    return uid


async def gated_user_id(ctx: Ctx, uid: int = Depends(active_user_id)) -> int:
    """Protected actions: not banned + documents accepted + subscribed to the channel (checked server-side)."""
    from backend.services import access

    await access.require_access(ctx, uid)  # raises AccessDenied -> 403 {detail, code}
    return uid


async def current_admin_id(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> int:
    try:
        uid, role = decode_token(ctx.settings.session_secret.get_secret_value(), _bearer(authorization))
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    # Role is re-checked on every request: revoking an admin takes effect immediately.
    if role != "admin" or not await roles.is_admin(ctx, uid):
        raise HTTPException(403, "Недостаточно прав")
    return uid


async def current_staff_id(ctx: Ctx, authorization: Annotated[str | None, Header()] = None) -> int:
    """Owner, admin or moderator. Endpoints using it must check a permission (require_perm) if needed."""
    try:
        uid, role = decode_token(ctx.settings.session_secret.get_secret_value(), _bearer(authorization))
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    if role != "admin" or not await roles.is_staff(ctx, uid):
        raise HTTPException(403, "Недостаточно прав")
    return uid


def require_perm(perm: str):
    async def dep(ctx: Ctx, uid: int = Depends(current_staff_id)) -> int:
        if not await roles.has_perm(ctx, uid, perm):
            raise HTTPException(403, "Недостаточно прав для этого действия")
        return uid

    return Depends(dep)


async def current_owner_id(ctx: Ctx, admin_id: int = Depends(current_admin_id)) -> int:
    if not await roles.is_owner(ctx, admin_id):
        raise HTTPException(403, "Действие доступно только главному администратору")
    return admin_id


UserId = Annotated[int, Depends(current_user_id)]
ActiveUserId = Annotated[int, Depends(active_user_id)]
GatedUserId = Annotated[int, Depends(gated_user_id)]
AdminId = Annotated[int, Depends(current_admin_id)]
OwnerId = Annotated[int, Depends(current_owner_id)]
StaffId = Annotated[int, Depends(current_staff_id)]

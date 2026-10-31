"""Telegram Mini App initData validation, session tokens and admin web login codes."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl

import jwt
from sqlalchemy import func, select

from backend.models import AdminLoginCode
from backend.services.context import AppContext, ServiceError
from backend.utils import utcnow


class AuthError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int, now: float | None = None) -> dict[str, Any]:
    """https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    if not init_data or not bot_token:
        raise AuthError("initData отсутствует")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("Нет подписи")
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise AuthError("Неверная подпись")
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("Некорректный auth_date") from exc
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age_seconds or auth_date - now > 300:
        raise AuthError("Данные авторизации устарели")
    try:
        user = json.loads(pairs.get("user", ""))
    except ValueError as exc:
        raise AuthError("Нет данных пользователя") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise AuthError("Нет данных пользователя")
    return user


def issue_token(secret: str, *, user_id: int, role: str, ttl_minutes: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "role": role, "iat": now, "exp": now + ttl_minutes * 60}, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "role"]})
    except jwt.PyJWTError as exc:
        raise AuthError("Сессия недействительна") from exc
    return int(payload["sub"]), str(payload["role"])


def _code_hash(secret: str, admin_id: int, code: str) -> str:
    return hmac.new(secret.encode(), f"{admin_id}:{code}".encode(), hashlib.sha256).hexdigest()


async def request_admin_code(ctx: AppContext, admin_id: int) -> None:
    """Send a one-time login code to an admin via the bot. Silently ignores non-admins."""
    from backend.services import roles

    if not await roles.is_staff(ctx, admin_id):
        return
    secret = ctx.settings.session_secret.get_secret_value()
    async with ctx.session() as session, session.begin():
        recent = await session.scalar(
            select(func.count(AdminLoginCode.id)).where(AdminLoginCode.admin_id == admin_id, AdminLoginCode.created_at > utcnow() - timedelta(minutes=10))
        )
        if int(recent or 0) >= 3:
            raise ServiceError("Слишком много запросов кода. Подождите 10 минут.", "rate_limited")
        code = f"{secrets.randbelow(10**6):06d}"
        session.add(AdminLoginCode(admin_id=admin_id, code_hash=_code_hash(secret, admin_id, code), expires_at=utcnow() + timedelta(minutes=5)))
    await ctx.notifier.send(admin_id, f"🔐 Код входа в панель SpirittVPN: <code>{code}</code>\nДействует 5 минут. Никому его не сообщайте.")


async def verify_admin_code(ctx: AppContext, admin_id: int, code: str) -> bool:
    from backend.services import roles

    if not await roles.is_staff(ctx, admin_id):
        return False
    secret = ctx.settings.session_secret.get_secret_value()
    async with ctx.session() as session, session.begin():
        row = await session.scalar(
            select(AdminLoginCode)
            .where(AdminLoginCode.admin_id == admin_id, AdminLoginCode.used_at.is_(None), AdminLoginCode.expires_at > utcnow())
            .order_by(AdminLoginCode.created_at.desc())
            .with_for_update()
        )
        if row is None or row.attempts >= 5:
            return False
        row.attempts += 1
        if not hmac.compare_digest(row.code_hash, _code_hash(secret, admin_id, code.strip())):
            return False
        row.used_at = utcnow()
        return True

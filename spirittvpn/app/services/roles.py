"""Staff roles and permissions. Every admin action calls require() server-side."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Staff

log = logging.getLogger("spirittvpn.roles")

ROLE_TITLES = {"owner": "Владелец", "admin": "Администратор", "support": "Поддержка", "tech": "Технический специалист"}

ALL_PERMS = frozenset({
    "stats.view", "users.view", "users.manage", "devices.manage", "plans.manage", "promos.manage",
    "referrals.manage", "nodes.view", "nodes.manage", "tickets", "broadcast", "settings.manage",
    "audit.view", "staff.manage", "payments.manage",
})

ROLE_PERMS: dict[str, frozenset[str]] = {
    "owner": ALL_PERMS,
    # Infrastructure management, refunds and staff management are not granted to admins by default.
    "admin": ALL_PERMS - {"staff.manage", "payments.manage", "nodes.manage"},
    "support": frozenset({"users.view", "tickets"}),
    "tech": frozenset({"stats.view", "users.view", "devices.manage", "nodes.view", "nodes.manage"}),
}


class AdminPermissionError(PermissionError):
    pass


async def get_role(session: AsyncSession, settings: Settings, telegram_id: int | None) -> str | None:
    if telegram_id is None:
        return None
    role = settings.env_role(telegram_id)
    if role:
        return role
    staff = await session.get(Staff, telegram_id)
    return staff.role if staff and staff.role in ROLE_PERMS else None


def perms_of(role: str | None) -> frozenset[str]:
    return ROLE_PERMS.get(role or "", frozenset())


async def require(session: AsyncSession, settings: Settings, telegram_id: int | None, perm: str) -> int:
    role = await get_role(session, settings, telegram_id)
    if perm not in perms_of(role):
        log.warning("denied %s for tg=%s (role=%s)", perm, telegram_id, role)
        raise AdminPermissionError(perm)
    assert telegram_id is not None
    return telegram_id

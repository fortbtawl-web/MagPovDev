"""Administrative roles.

Sources of admin rights, checked on every request:
  * OWNER_TELEGRAM_ID (env)     -> owner, cannot be revoked from the panel
  * admin_roles row role=owner  -> owner (bootstrapped once via /setup when no env owner is set)
  * ADMIN_IDS (env)             -> admin
  * admin_roles row role=admin  -> admin (granted/revoked by an owner)
  * admin_roles row role=moderator -> moderator: users (read-only), support, task review
Usernames are never used for authorisation. Every protected action checks a permission (has_perm) on the server.
"""
from __future__ import annotations

import logging
import secrets

from sqlalchemy import select

from backend.models import AdminRole, User
from backend.services.context import AppContext, ServiceError

log = logging.getLogger("spiritt.roles")


async def get_role(ctx: AppContext, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    s = ctx.settings
    if user_id in s.owner_id_set:
        return "owner"
    async with ctx.session() as session:
        row = await session.get(AdminRole, user_id)
    if row is not None:
        return row.role
    if user_id in s.admin_id_set:
        return "admin"
    return None


STAFF_PERMS = frozenset({"users.view", "support", "tasks.review"})
ADMIN_PERMS = STAFF_PERMS | {"users.manage", "coins.manage", "tasks.manage", "plans.manage", "nodes", "settings"}
OWNER_PERMS = ADMIN_PERMS | {"roles.manage", "economy.limits"}
PERMISSIONS: dict[str, frozenset[str]] = {"owner": frozenset(OWNER_PERMS), "admin": frozenset(ADMIN_PERMS), "moderator": STAFF_PERMS}
ROLE_LABELS = {"owner": "👑 Владелец", "admin": "🛠 Администратор", "moderator": "🛡 Модератор"}


def perms_for(role: str | None) -> frozenset[str]:
    return PERMISSIONS.get(role or "", frozenset())


async def effective_perms(ctx: AppContext, user_id: int | None) -> frozenset[str]:
    """Static role permissions plus the ones the owner delegated through settings."""
    role = await get_role(ctx, user_id)
    perms = set(perms_for(role))
    if role == "owner":
        perms.add("trialcodes.manage")
    elif role == "admin":
        from backend.services import settings as settings_svc

        async with ctx.session() as session:
            if await settings_svc.get(session, "trial_codes_admin_access"):
                perms.add("trialcodes.manage")
    return frozenset(perms)


async def has_perm(ctx: AppContext, user_id: int | None, perm: str) -> bool:
    return perm in await effective_perms(ctx, user_id)


async def is_staff(ctx: AppContext, user_id: int | None) -> bool:
    """Any panel role, including moderator."""
    return await get_role(ctx, user_id) is not None


async def is_admin(ctx: AppContext, user_id: int | None) -> bool:
    """Owner or admin (moderators are not admins)."""
    return await get_role(ctx, user_id) in ("owner", "admin")


async def is_owner(ctx: AppContext, user_id: int | None) -> bool:
    return await get_role(ctx, user_id) == "owner"


async def admin_ids(ctx: AppContext) -> set[int]:
    async with ctx.session() as session:
        db_ids = set((await session.execute(select(AdminRole.user_id))).scalars())
    return db_ids | set(ctx.settings.admin_id_set)


async def list_admins(ctx: AppContext) -> list[dict]:
    s = ctx.settings
    async with ctx.session() as session:
        rows = (await session.execute(select(AdminRole))).scalars().all()
        entries: dict[int, dict] = {}
        for oid in s.owner_id_set:
            entries[oid] = {"user_id": oid, "role": "owner", "source": "env", "granted_by": None, "granted_at": None}
        for uid in s.admin_id_set:
            entries.setdefault(uid, {"user_id": uid, "role": "admin", "source": "env", "granted_by": None, "granted_at": None})
        for r in rows:
            if r.user_id in entries and entries[r.user_id]["source"] == "env" and entries[r.user_id]["role"] == "owner":
                continue
            entries[r.user_id] = {"user_id": r.user_id, "role": r.role, "source": "db", "granted_by": r.granted_by, "granted_at": r.granted_at}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(list(entries))))).scalars()} if entries else {}
    out = []
    for uid, e in entries.items():
        u = users.get(uid)
        out.append({**e, "username": u.username if u else None, "first_name": u.first_name if u else None, "public_id": u.public_id if u else None})
    return sorted(out, key=lambda e: (e["role"] != "owner", e["user_id"]))


async def grant_admin(ctx: AppContext, actor_id: int, target_id: int, reason: str, source: str, *, role: str = "admin") -> dict:
    from backend.services import admin as admin_svc

    if role not in ("admin", "moderator"):
        raise ServiceError("Роль: admin или moderator.", "bad_role")
    if not await is_owner(ctx, actor_id):
        raise ServiceError("Назначать администраторов и модераторов может только владелец.", "forbidden")
    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        user = await session.get(User, target_id)
        if user is None:
            raise ServiceError("Пользователь не найден. Он должен хотя бы раз открыть бота.", "not_found")
        if user.is_banned:
            raise ServiceError("Нельзя назначить администратором заблокированного пользователя.", "banned")
        current = await get_role(ctx, target_id)  # DB role, ADMIN_IDS or OWNER_TELEGRAM_ID
        row = await session.get(AdminRole, target_id, with_for_update=True)
        if current == role:
            raise ServiceError(f"У пользователя уже роль {ROLE_LABELS[role]}.", "already")
        if current == "owner" or (current is not None and row is None):
            raise ServiceError("Роль этого пользователя задана владельцем или в .env — её нельзя изменить здесь.", "already")
        if row is None:
            session.add(AdminRole(user_id=target_id, role=role, granted_by=actor_id))
        else:  # admin <-> moderator
            row.role, row.granted_by = role, actor_id
        admin_svc.audit(session, admin_id=actor_id, source=source, action="admin.grant" if role == "admin" else "moderator.grant", entity_type="user", entity_id=target_id, reason=reason, old={"role": current}, new={"role": role})
    await ctx.notifier.send(target_id, f"{ROLE_LABELS[role]}: вам выданы права в SpirittVPN. Откройте /admin.")
    return {"user_id": target_id, "role": role, "source": "db", "granted_by": actor_id, "username": user.username, "first_name": user.first_name, "public_id": user.public_id, "granted_at": None}


async def grant_owner(ctx: AppContext, actor_id: int, target_id: int, reason: str, source: str) -> dict:
    """Give full owner rights. Only an existing owner can do it; works for new users and for existing admins."""
    from backend.services import admin as admin_svc

    if not await is_owner(ctx, actor_id):
        raise ServiceError("Выдавать права владельца может только владелец.", "forbidden")
    reason = admin_svc.require_reason(reason)
    async with ctx.session() as session, session.begin():
        user = await session.get(User, target_id)
        if user is None:
            raise ServiceError("Пользователь не найден. Он должен хотя бы раз открыть бота.", "not_found")
        if user.is_banned:
            raise ServiceError("Нельзя назначить владельцем заблокированного пользователя.", "banned")
        if target_id in ctx.settings.owner_id_set:
            raise ServiceError("Пользователь уже владелец (задан в .env).", "already")
        row = await session.get(AdminRole, target_id, with_for_update=True)
        old = row.role if row else None
        if old == "owner":
            raise ServiceError("Пользователь уже владелец.", "already")
        if row is None:
            session.add(AdminRole(user_id=target_id, role="owner", granted_by=actor_id))
        else:
            row.role, row.granted_by = "owner", actor_id
        admin_svc.audit(session, admin_id=actor_id, source=source, action="admin.grant_owner", entity_type="user", entity_id=target_id, reason=reason, old={"role": old}, new={"role": "owner"})
    await ctx.notifier.send(target_id, "👑 Вам выданы права владельца SpirittVPN. Откройте /admin.")
    return {"user_id": target_id, "role": "owner", "source": "db", "granted_by": actor_id, "username": user.username, "first_name": user.first_name, "public_id": user.public_id, "granted_at": None}


async def revoke_admin(ctx: AppContext, actor_id: int, target_id: int, reason: str, source: str) -> None:
    from backend.services import admin as admin_svc

    if not await is_owner(ctx, actor_id):
        raise ServiceError("Снимать права может только главный администратор.", "forbidden")
    reason = admin_svc.require_reason(reason)
    if target_id == actor_id:
        raise ServiceError("Нельзя снять права с самого себя.", "self")
    async with ctx.session() as session, session.begin():
        row = await session.get(AdminRole, target_id, with_for_update=True)
        if row is None:
            if target_id in ctx.settings.admin_id_set:
                raise ServiceError("Этот администратор задан в .env сервера (ADMIN_IDS / OWNER_TELEGRAM_ID) — измените конфигурацию.", "env_admin")
            raise ServiceError("Пользователь не администратор.", "not_admin")
        if row.role == "owner":
            # Owners are never removed from the panel, so the project can't be left without an owner.
            raise ServiceError("Владельца нельзя снять из панели.", "owner")
        old_role = row.role
        await session.delete(row)
        admin_svc.audit(session, admin_id=actor_id, source=source, action="admin.revoke", entity_type="user", entity_id=target_id, reason=reason, old={"role": old_role}, new={"role": None})
    await ctx.notifier.send(target_id, "Права администратора SpirittVPN сняты.")


# ---- one-time owner bootstrap (only when no owner is configured at all) ----

async def owner_exists(ctx: AppContext) -> bool:
    if ctx.settings.owner_id_set:
        return True
    async with ctx.session() as session:
        return await session.scalar(select(AdminRole.user_id).where(AdminRole.role == "owner")) is not None


def new_setup_code() -> str:
    return secrets.token_hex(8)


async def claim_owner(ctx: AppContext, user_id: int, code: str, expected: str | None) -> None:
    """The code is printed only in the server log, so claiming requires access to the server (owner confirmation)."""
    if not expected or not secrets.compare_digest(code.strip(), expected):
        raise ServiceError("Неверный код настройки.", "bad_code")
    if await owner_exists(ctx):
        raise ServiceError("Главный администратор уже назначен.", "owner_exists")
    async with ctx.session() as session, session.begin():
        if await session.get(User, user_id) is None:
            raise ServiceError("Сначала нажмите /start.", "not_found")
        row = await session.get(AdminRole, user_id)
        if row is None:
            session.add(AdminRole(user_id=user_id, role="owner", granted_by=None))
        else:
            row.role = "owner"
        from backend.services import admin as admin_svc

        admin_svc.audit(session, admin_id=user_id, source="bot", action="admin.claim_owner", entity_type="user", entity_id=user_id, reason="первоначальная настройка через /setup", new={"role": "owner"})
    log.warning("owner claimed by telegram id %s via /setup", user_id)

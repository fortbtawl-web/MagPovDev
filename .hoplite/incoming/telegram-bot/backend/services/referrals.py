"""Registration and referral program."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import BonusTransaction, Order, OrderStatus, Referral, ReferralReward, ReferralVisit, User
from backend.services import settings as settings_svc
from backend.utils import utcnow

log = logging.getLogger("spiritt.referrals")
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def new_referral_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


# No 0/O/1/I/L: easy to dictate to support. 32^6 ≈ 1e9 random IDs, not sequential.
PUBLIC_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_public_id() -> str:
    return "SV-" + "".join(secrets.choice(PUBLIC_ALPHABET) for _ in range(6))


def normalize_public_id(text: str) -> str | None:
    t = text.strip().upper().replace(" ", "")
    if t.startswith("SV-"):
        t = t[3:]
    elif t.startswith("SV") and len(t) == 8:
        t = t[2:]
    if len(t) == 6 and all(c in PUBLIC_ALPHABET for c in t):
        return "SV-" + t
    return None


@dataclass
class TgProfile:
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


@dataclass
class Registration:
    user: User
    created: bool
    referrer_id: int | None = None
    referral_note: str | None = None


def parse_ref_arg(arg: str | None) -> str | None:
    if not arg:
        return None
    arg = arg.strip()
    if not arg.startswith("ref_"):
        return None
    token = arg[4:]
    if not token or len(token) > 32 or not token.isalnum():
        return None
    return token


async def _resolve_referrer(session: AsyncSession, token: str) -> User | None:
    user = await session.scalar(select(User).where(User.referral_code == token.lower()))
    if user is None and token.isdigit() and len(token) <= 15:
        # Also accept ref_<telegram id> links.
        user = await session.get(User, int(token))
    return user


async def register_user(session: AsyncSession, profile: TgProfile, start_arg: str | None = None) -> Registration:
    """Idempotent /start. Referral is attached only when the user row is created in this call."""
    now = utcnow()
    for _ in range(5):
        stmt = (
            insert(User)
            .values(
                id=profile.id,
                username=profile.username,
                first_name=profile.first_name,
                last_name=profile.last_name,
                language_code=profile.language_code,
                referral_code=new_referral_code(),
                public_id=new_public_id(),
                last_seen_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.id])
            .returning(User.id)
        )
        try:
            async with session.begin_nested():
                inserted = (await session.execute(stmt)).scalar_one_or_none()
            break
        except Exception as exc:  # referral_code / public_id collision: retry with new codes
            if "referral_code" not in str(exc) and "public_id" not in str(exc):
                raise
    else:  # pragma: no cover
        raise RuntimeError("Не удалось сгенерировать реферальный код")

    user = await session.get(User, profile.id, populate_existing=True)
    assert user is not None
    token = parse_ref_arg(start_arg)
    if inserted is None:
        user.username = profile.username
        user.first_name = profile.first_name
        user.last_name = profile.last_name
        user.last_seen_at = now
        if token:  # counted as a visit, but the original referrer is never replaced
            referrer = await _resolve_referrer(session, token)
            if referrer is not None and referrer.id != user.id:
                await _record_visit(session, referrer.id, user.id, "existing")
        return Registration(user=user, created=False)

    reg = Registration(user=user, created=True)
    if token:
        referrer = await _resolve_referrer(session, token)
        if referrer is None:
            reg.referral_note = "invalid"
        elif referrer.id == user.id:
            reg.referral_note = "self"
        else:
            res = await session.execute(
                insert(Referral)
                .values(referrer_id=referrer.id, referred_id=user.id)
                .on_conflict_do_nothing(index_elements=[Referral.referred_id])
                .returning(Referral.id)
            )
            if res.scalar_one_or_none() is not None:
                reg.referrer_id = referrer.id
                reg.referral_note = "ok"
            await _record_visit(session, referrer.id, user.id, "registered")
    return reg


async def _record_visit(session: AsyncSession, referrer_id: int, visitor_id: int, outcome: str) -> None:
    now = utcnow()
    stmt = insert(ReferralVisit).values(referrer_id=referrer_id, visitor_id=visitor_id, outcome=outcome, first_at=now, last_at=now)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_referral_visits_pair", set_={"visits": ReferralVisit.visits + 1, "last_at": now}
    )
    await session.execute(stmt)


async def referrer_of(session: AsyncSession, user_id: int) -> int | None:
    return await session.scalar(select(Referral.referrer_id).where(Referral.referred_id == user_id))


def _mask(name: str | None) -> str:
    name = (name or "Пользователь").strip() or "Пользователь"
    return name[:1] + "•" * max(2, min(len(name) - 1, 6))


async def referral_stats(session: AsyncSession, referrer_id: int) -> dict:
    """Derived from orders, so a re-delivered payment webhook can never be counted twice."""
    from backend.utils import fmt_money

    visits = await session.scalar(select(func.count(ReferralVisit.id)).where(ReferralVisit.referrer_id == referrer_id))
    reg, trial = (await session.execute(
        select(func.count(Referral.id), func.count(Referral.trial_activated_at)).where(Referral.referrer_id == referrer_id)
    )).one()
    money = (Order.referrer_id == referrer_id) & (Order.currency != "SPC")
    purchases, paid, buyers = (await session.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount_minor), 0), func.count(func.distinct(Order.user_id)))
        .where(money, Order.status == OrderStatus.PAID)
    )).one()
    refunds = await session.scalar(select(func.count(Order.id)).where(money, Order.status == OrderStatus.REFUNDED))
    coin_purchases = await session.scalar(
        select(func.count(Order.id)).where(Order.referrer_id == referrer_id, Order.currency == "SPC", Order.status == OrderStatus.PAID)
    )
    return {
        "visits": int(visits or 0), "registered": int(reg or 0), "trial": int(trial or 0), "buyers": int(buyers or 0),
        "purchases": int(purchases or 0), "paid_minor": int(paid or 0), "paid_label": fmt_money(int(paid or 0)),
        "coin_purchases": int(coin_purchases or 0), "refunds": int(refunds or 0),
    }


async def referral_events(session: AsyncSession, referrer_id: int, *, limit: int = 50, mask: bool = True) -> list[dict]:
    from backend.utils import fmt_money

    name = (lambda n: _mask(n)) if mask else (lambda n: n or "Пользователь")
    events: list[dict] = []
    for v, u in (await session.execute(
        select(ReferralVisit, User).join(User, User.id == ReferralVisit.visitor_id)
        .where(ReferralVisit.referrer_id == referrer_id, ReferralVisit.outcome == "existing").order_by(ReferralVisit.first_at.desc()).limit(limit)
    )).all():
        events.append({"kind": "visit", "label": "Переход (уже был в боте)", "who": name(u.first_name), "detail": None, "at": v.first_at})
    for r, u in (await session.execute(
        select(Referral, User).join(User, User.id == Referral.referred_id).where(Referral.referrer_id == referrer_id).order_by(Referral.created_at.desc()).limit(limit)
    )).all():
        events.append({"kind": "registered", "label": "Регистрация по ссылке", "who": name(u.first_name), "detail": None, "at": r.created_at})
        if r.trial_activated_at:
            events.append({"kind": "trial", "label": "Пробный период", "who": name(u.first_name), "detail": None, "at": r.trial_activated_at})
    for o, u in (await session.execute(
        select(Order, User).join(User, User.id == Order.user_id)
        .where(Order.referrer_id == referrer_id, Order.status.in_((OrderStatus.PAID, OrderStatus.REFUNDED))).order_by(Order.created_at.desc()).limit(limit)
    )).all():
        coins = o.currency == "SPC"
        kind = "refund" if o.status == OrderStatus.REFUNDED else "coin_purchase" if coins else "purchase"
        label = {"refund": "Возврат оплаты", "coin_purchase": "Покупка за SpirCoin", "purchase": "Оплата подписки"}[kind]
        amount = f"{o.amount_minor} SpirCoin" if coins else fmt_money(o.amount_minor, o.currency)
        events.append({"kind": kind, "label": label, "who": name(u.first_name), "detail": f"{o.plan_title} · {amount}", "at": o.paid_at or o.created_at})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


async def grant_reward(session: AsyncSession, referred_id: int, event_type: str, order_id: int | None = None) -> ReferralReward | None:
    """Called inside the transaction that confirmed the trial/payment. Unique constraints make it once-only."""
    referral = await session.scalar(select(Referral).where(Referral.referred_id == referred_id).with_for_update())
    if referral is None:
        return None
    now = utcnow()
    if event_type == "trial_activated":
        referral.trial_activated_at = referral.trial_activated_at or now
        enabled_key, days_key, kind = "referral_trial_bonus_enabled", "referral_trial_bonus_days", "referral_trial"
    elif event_type == "first_payment":
        referral.first_paid_at = referral.first_paid_at or now
        enabled_key, days_key, kind = "referral_payment_bonus_enabled", "referral_payment_bonus_days", "referral_payment"
    else:
        raise ValueError(event_type)

    if not await settings_svc.get(session, "referral_enabled") or not await settings_svc.get(session, enabled_key):
        return None
    days = int(await settings_svc.get(session, days_key))
    res = await session.execute(
        insert(ReferralReward)
        .values(referral_id=referral.id, referrer_id=referral.referrer_id, event_type=event_type, days=days, order_id=order_id)
        .on_conflict_do_nothing(constraint="uq_referral_rewards_once")
        .returning(ReferralReward.id)
    )
    reward_id = res.scalar_one_or_none()
    if reward_id is None:
        return None
    session.add(BonusTransaction(user_id=referral.referrer_id, days=days, kind=kind, referral_reward_id=reward_id))
    await session.flush()
    log.info("referral reward %s: referrer=%s referred=%s days=%s", event_type, referral.referrer_id, referred_id, days)
    return await session.get(ReferralReward, reward_id)


async def bonus_balance(session: AsyncSession, user_id: int) -> int:
    total = await session.scalar(select(func.coalesce(func.sum(BonusTransaction.days), 0)).where(BonusTransaction.user_id == user_id))
    return int(total or 0)


async def referral_summary(session: AsyncSession, user_id: int) -> dict[str, int]:
    row = (
        await session.execute(
            select(
                func.count(Referral.id),
                func.count(Referral.trial_activated_at),
                func.count(Referral.first_paid_at),
            ).where(Referral.referrer_id == user_id)
        )
    ).one()
    earned = await session.scalar(
        select(func.coalesce(func.sum(BonusTransaction.days), 0)).where(
            BonusTransaction.user_id == user_id, BonusTransaction.kind.in_(("referral_trial", "referral_payment", "reversal"))
        )
    )
    invited, trials, paid = int(row[0]), int(row[1]), int(row[2])
    pending = await session.scalar(
        select(func.count(Referral.id)).where(Referral.referrer_id == user_id, Referral.first_paid_at.is_(None))
    )
    return {
        "invited": invited,
        "with_trial": trials,
        "with_payment": paid,
        "earned_days": int(earned or 0),
        "pending": int(pending or 0),
        "balance_days": await bonus_balance(session, user_id),
    }


async def list_invites(session: AsyncSession, user_id: int, limit: int = 50) -> list[dict]:
    rows = (
        await session.execute(
            select(Referral, User.first_name)
            .join(User, User.id == Referral.referred_id)
            .where(Referral.referrer_id == user_id)
            .order_by(Referral.created_at.desc())
            .limit(limit)
        )
    ).all()
    rewards = {}
    if rows:
        rr = (
            await session.execute(
                select(ReferralReward).where(ReferralReward.referral_id.in_([r.Referral.id for r in rows]))
            )
        ).scalars()
        for reward in rr:
            if reward.status == "granted":
                rewards[reward.referral_id] = rewards.get(reward.referral_id, 0) + reward.days
    result = []
    for r in rows:
        name = (r.first_name or "Пользователь").strip()
        result.append(
            {
                # Only a masked first name is shown to protect the invitee's privacy.
                "name": (name[:1] + "•" * max(2, min(len(name) - 1, 6))) if name else "Пользователь",
                "registered_at": r.Referral.created_at,
                "trial": r.Referral.trial_activated_at is not None,
                "paid": r.Referral.first_paid_at is not None,
                "bonus_days": rewards.get(r.Referral.id, 0),
            }
        )
    return result


async def bonus_history(session: AsyncSession, user_id: int, limit: int = 50) -> list[BonusTransaction]:
    return list(
        (
            await session.execute(
                select(BonusTransaction)
                .where(BonusTransaction.user_id == user_id)
                .order_by(BonusTransaction.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )


BONUS_KIND_LABELS = {
    "referral_trial": "Друг активировал пробный период",
    "referral_payment": "Первая оплата друга",
    "admin_adjust": "Корректировка администратора",
    "applied": "Применено к подписке",
    "reversal": "Отмена начисления",
}


async def admin_overview(session: AsyncSession, *, top: int = 50) -> tuple[dict, list[dict]]:
    from backend.utils import fmt_money

    money = (Order.referrer_id.is_not(None)) & (Order.currency != "SPC")
    referrers = await session.scalar(select(func.count(func.distinct(Referral.referrer_id))))
    visits = await session.scalar(select(func.count(ReferralVisit.id)))
    registered = await session.scalar(select(func.count(Referral.id)))
    purchases, paid, buyers = (await session.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount_minor), 0), func.count(func.distinct(Order.user_id))).where(money, Order.status == OrderStatus.PAID)
    )).one()
    refunds = await session.scalar(select(func.count(Order.id)).where(money, Order.status == OrderStatus.REFUNDED))
    coin = await session.scalar(select(func.count(Order.id)).where(Order.referrer_id.is_not(None), Order.currency == "SPC", Order.status == OrderStatus.PAID))
    overview = {
        "referrers": int(referrers or 0), "visits": int(visits or 0), "registered": int(registered or 0), "buyers": int(buyers or 0),
        "purchases": int(purchases or 0), "paid_minor": int(paid or 0), "paid_label": fmt_money(int(paid or 0)),
        "coin_purchases": int(coin or 0), "refunds": int(refunds or 0),
    }
    reg_sq = select(Referral.referrer_id.label("rid"), func.count(Referral.id).label("registered")).group_by(Referral.referrer_id).subquery()
    ord_sq = (
        select(Order.referrer_id.label("rid"), func.count(Order.id).label("purchases"), func.count(func.distinct(Order.user_id)).label("buyers"),
               func.coalesce(func.sum(Order.amount_minor), 0).label("paid"))
        .where(money, Order.status == OrderStatus.PAID).group_by(Order.referrer_id).subquery()
    )
    rows = (await session.execute(
        select(User, reg_sq.c.registered, ord_sq.c.purchases, ord_sq.c.buyers, ord_sq.c.paid)
        .join(reg_sq, reg_sq.c.rid == User.id).outerjoin(ord_sq, ord_sq.c.rid == User.id)
        .order_by(func.coalesce(ord_sq.c.paid, 0).desc(), reg_sq.c.registered.desc()).limit(top)
    )).all()
    top_rows = [{
        "user_id": u.id, "user_name": u.display_name, "username": u.username, "public_id": u.public_id,
        "registered": int(r or 0), "buyers": int(b or 0), "purchases": int(p or 0), "paid_minor": int(m or 0), "paid_label": fmt_money(int(m or 0)),
    } for u, r, p, b, m in rows]
    return overview, top_rows


async def admin_user(session: AsyncSession, user: User) -> dict:
    from backend.api.serializers import ts
    from backend.services.payments import ORDER_STATUS_LABELS
    from backend.utils import fmt_money

    invitees = []
    for ref, u in (await session.execute(
        select(Referral, User).join(User, User.id == Referral.referred_id).where(Referral.referrer_id == user.id).order_by(Referral.created_at.desc()).limit(500)
    )).all():
        n, s = (await session.execute(
            select(func.count(Order.id), func.coalesce(func.sum(Order.amount_minor), 0))
            .where(Order.user_id == u.id, Order.referrer_id == user.id, Order.status == OrderStatus.PAID, Order.currency != "SPC")
        )).one()
        invitees.append({"user_id": u.id, "name": u.display_name, "username": u.username, "registered_at": ts(ref.created_at),
                         "trial": ref.trial_activated_at is not None, "purchases": int(n), "paid_label": fmt_money(int(s))})
    orders = [{
        "id": o.id, "user_id": o.user_id, "plan_title": o.plan_title,
        "amount_label": f"{o.amount_minor} SpirCoin" if o.currency == "SPC" else fmt_money(o.amount_minor, o.currency),
        "currency": o.currency, "status": o.status, "status_label": ORDER_STATUS_LABELS.get(o.status, o.status), "promo_code": o.promo_code,
        "created_at": ts(o.created_at), "paid_at": ts(o.paid_at),
    } for o in (await session.execute(select(Order).where(Order.referrer_id == user.id).order_by(Order.created_at.desc()).limit(500))).scalars()]
    return {"stats": await referral_stats(session, user.id), "invitees": invitees, "orders": orders}


async def export_rows(session: AsyncSession) -> list[list]:
    _, top = await admin_overview(session, top=100_000)
    return [[r["user_id"], r["username"] or "", r["public_id"], r["registered"], r["buyers"], r["purchases"], f"{r['paid_minor'] / 100:.2f}"] for r in top]

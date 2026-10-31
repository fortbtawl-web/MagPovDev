"""Model -> JSON helpers. Never include secrets (node tokens, payment keys)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.models import AuditLog, BonusTransaction, Device, Order, Plan, ReferralReward, Subscription, User, VpnOperation
from backend.services.payments import ORDER_STATUS_LABELS, plan_terms
from backend.services.referrals import BONUS_KIND_LABELS
from backend.utils import fmt_money


def ts(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


SUB_STATUS_LABELS = {
    "pending": "Оформляется",
    "active": "Активна",
    "expired": "Истекла",
    "suspended": "Приостановлена",
    "revoked": "Отозвана",
    "failed": "Ошибка",
}
DEVICE_STATUS_LABELS = {
    "pending": "Создаётся на сервере",
    "active": "Активно",
    "expired": "Подписка истекла",
    "suspended": "Приостановлено",
    "revoking": "Отключается",
    "revoked": "Отключено",
    "failed": "Ошибка создания",
}


def user(u: User, role: str | None = None) -> dict[str, Any]:
    return {
        "id": u.id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name, "created_at": ts(u.created_at),
        "is_banned": u.is_banned, "public_id": u.public_id, "role": role,
    }


EVENT_LABELS = {
    "created": "Подписка оформлена",
    "extended": "Продление",
    "extended_suspended": "Продление",
    "reactivated": "Подписка возобновлена",
    "lifetime": "Бессрочный доступ",
    "expired": "Срок истёк",
    "suspended": "Доступ приостановлен",
    "resumed": "Доступ восстановлен",
    "blocked": "Аккаунт заблокирован",
    "unblocked": "Аккаунт разблокирован",
    "bonus_applied": "Применены бонусные дни",
    "admin_add_days": "Продление администратором",
    "admin_set_expiry": "Изменена дата окончания",
    "admin_device_limit": "Изменён лимит устройств",
    "admin_extend": "Продление администратором",
    "admin_replace": "Новая подписка от администратора",
    "admin_set_end": "Изменена дата окончания",
    "admin_lifetime": "Бессрочный доступ от администратора",
    "admin_shorten": "Срок сокращён",
    "admin_terminate": "Подписка завершена досрочно",
    "admin_revoke": "Доступ отозван",
    "noop_lifetime": "Оплата при бессрочной подписке",
    "promo_days": "Бесплатные дни по промокоду",
}


def sub_event(e, *, admin: bool = False) -> dict[str, Any]:
    data = {
        "id": e.id, "action": e.action, "action_label": EVENT_LABELS.get(e.action, e.action),
        "old_expires_at": ts(e.old_expires_at), "new_expires_at": ts(e.new_expires_at), "days": e.days,
        "note": e.public_note, "created_at": ts(e.created_at),
    }
    if admin:
        data.update({"old_status": e.old_status, "new_status": e.new_status, "actor": e.actor, "comment": e.comment})
    return data


def ticket(t, preview: str | None = None, *, admin_user: User | None = None) -> dict[str, Any]:
    from backend.services.support import CATEGORIES, STATUS_LABELS

    data = {
        "id": t.id, "number": t.number, "category": t.category, "category_label": CATEGORIES.get(t.category, t.category),
        "subject": t.subject, "status": t.status, "status_label": STATUS_LABELS.get(t.status, t.status),
        "created_at": ts(t.created_at), "updated_at": ts(t.updated_at), "last_message_preview": preview,
    }
    if admin_user is not None:
        data.update({"user_id": t.user_id, "user_public_id": admin_user.public_id, "user_name": admin_user.display_name, "assigned_admin_id": t.assigned_admin_id})
    return data


def user_message(m) -> dict[str, Any]:
    author = {"user": "user", "admin": "support", "system": "system"}[m.author_type]
    return {"id": m.id, "author": author, "text": m.body, "has_attachment": m.attachment_file_id is not None, "created_at": ts(m.created_at)}


def admin_message(m) -> dict[str, Any]:
    return {
        "id": m.id, "author_type": m.author_type, "author_id": m.author_id, "text": m.body, "is_internal": m.is_internal,
        "has_attachment": m.attachment_file_id is not None, "created_at": ts(m.created_at),
    }


def subscription(s: Subscription | None, used: int | None = None) -> dict[str, Any] | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "status": s.status,
        "status_label": "Бессрочная подписка" if s.is_lifetime and s.status == "active" else SUB_STATUS_LABELS.get(s.status, s.status),
        "plan_code": s.plan_code,
        "is_lifetime": s.is_lifetime,
        "device_limit": s.device_limit,
        "devices_used": used,
        "source": s.source,
        "starts_at": ts(s.starts_at),
        "expires_at": ts(s.expires_at),
        "created_at": ts(s.created_at),
        "user_id": s.user_id,
        "remaining_seconds": _remaining(s),
    }


def _remaining(s: Subscription) -> int | None:
    from backend.utils import utcnow

    if s.is_lifetime or s.expires_at is None:
        return None
    return max(0, int((s.expires_at - utcnow()).total_seconds()))


def device(d: Device, *, with_links: bool = False) -> dict[str, Any]:
    data = {
        "id": d.id,
        "user_id": d.user_id,
        "label": d.label,
        "status": d.status,
        "status_label": DEVICE_STATUS_LABELS.get(d.status, d.status),
        "protocol": d.protocol.upper(),
        "node": {"code": d.node.code, "name": d.node.name, "flag": d.node.flag} if d.node else None,
        "created_at": ts(d.created_at),
        "expires_at": ts(d.node_expires_at),
        "last_error": d.last_error,
        "client_name": d.client_name,
    }
    if with_links:
        data["subscription_url"] = d.subscription_url
        data["link"] = d.connection_link
    return data


def plan(p: Plan) -> dict[str, Any]:
    return {
        "id": p.id,
        "code": p.code,
        "title": p.title,
        "description": p.description,
        "price_minor": p.price_minor,
        "price_label": fmt_money(p.price_minor, p.currency),
        "currency": p.currency,
        "duration_days": p.duration_days,
        "is_lifetime": p.is_lifetime,
        "device_limit": p.device_limit,
        "is_active": p.is_active,
        "sort_order": p.sort_order,
        "price_coins": p.price_coins,
        "terms": plan_terms(p),
    }


def coin_tx(t, *, user=None) -> dict[str, Any]:
    from backend.services.coins import KIND_LABELS

    data = {
        "id": t.id, "user_id": t.user_id, "amount": t.amount, "balance_after": t.balance_after, "kind": t.kind,
        "kind_label": KIND_LABELS.get(t.kind, t.kind), "reason": t.reason, "actor": t.actor, "order_id": t.order_id,
        "created_at": ts(t.created_at),
    }
    if user is not None:
        data["user_name"] = user.display_name
    return data


def order(o: Order) -> dict[str, Any]:
    pay = max(o.payments, key=lambda p: p.id) if o.payments else None
    return {
        "id": o.id,
        "user_id": o.user_id,
        "plan_code": o.plan_code,
        "plan_title": o.plan_title,
        "amount_minor": o.amount_minor,
        "amount_label": fmt_money(o.amount_minor, o.currency),
        "discount_minor": o.discount_minor,
        "promo_code": o.promo_activation.promo.code if o.promo_activation is not None else None,
        "status": o.status,
        "status_label": ORDER_STATUS_LABELS.get(o.status, o.status),
        "fulfillment_status": o.fulfillment_status,
        "fulfillment_error": o.fulfillment_error,
        "created_at": ts(o.created_at),
        "paid_at": ts(o.paid_at),
        "payment": {
            "provider": pay.provider,
            "provider_payment_id": pay.provider_payment_id,
            "status": pay.status,
            "confirmation_url": pay.confirmation_url if o.status == "pending" else None,
            "is_test": pay.is_test,
            "confirmed_at": ts(pay.confirmed_at),
            "error": pay.error,
        }
        if pay
        else None,
    }


def bonus(b: BonusTransaction) -> dict[str, Any]:
    return {"id": b.id, "days": b.days, "kind": b.kind, "label": BONUS_KIND_LABELS.get(b.kind, b.kind), "comment": b.comment, "created_at": ts(b.created_at)}


def reward(r: ReferralReward) -> dict[str, Any]:
    return {
        "id": r.id,
        "referrer_id": r.referrer_id,
        "referral_id": r.referral_id,
        "event_type": r.event_type,
        "days": r.days,
        "status": r.status,
        "created_at": ts(r.created_at),
        "reversed_at": ts(r.reversed_at),
        "reverse_reason": r.reverse_reason,
    }


def audit(a: AuditLog) -> dict[str, Any]:
    return {
        "id": a.id,
        "admin_id": a.admin_id,
        "source": a.source,
        "action": a.action,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "reason": a.reason,
        "old_value": a.old_value,
        "new_value": a.new_value,
        "created_at": ts(a.created_at),
    }


def vpn_op(o: VpnOperation) -> dict[str, Any]:
    return {
        "id": o.id,
        "device_id": o.device_id,
        "node_id": o.node_id,
        "op_type": o.op_type,
        "status": o.status,
        "attempts": o.attempts,
        "last_error": o.last_error,
        "next_retry_at": ts(o.next_retry_at),
        "actor": o.actor,
        "created_at": ts(o.created_at),
        "updated_at": ts(o.updated_at),
    }

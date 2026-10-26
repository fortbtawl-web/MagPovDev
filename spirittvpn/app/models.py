"""Database schema."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, utcnow


class SubStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"  # temporarily disabled by staff


LIFETIME_UNTIL = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)  # stored expiry for lifetime subscriptions


class DeviceStatus:
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"  # subscription inactive or over the device limit
    REVOKED = "revoked"

    LIVE = (PENDING, ACTIVE, SUSPENDED)


class OrderStatus:
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REFUNDED = "refunded"

    PAYABLE = (CREATED, PENDING)


class PromoKind:
    DAYS = "days"
    PERCENT = "percent"
    FIXED = "fixed"

    ALL = (DAYS, PERCENT, FIXED)
    DISCOUNTS = (PERCENT, FIXED)


class ActivationStatus:
    APPLIED = "applied"  # days bonus already added
    PENDING = "pending"  # discount waiting for the next paid order
    USED = "used"  # discount consumed by a paid order


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    group: Mapped[str | None] = mapped_column(String(32))
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    sounds_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"
    __table_args__ = (UniqueConstraint("user_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TrialActivation(Base):
    """One row per user ever — the UNIQUE constraint is the anti-abuse guarantee."""

    __tablename__ = "trial_activations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    activated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan_code: Mapped[str | None] = mapped_column(String(32))
    plan_title: Mapped[str | None] = mapped_column(String(128))
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    device_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), default=SubStatus.ACTIVE, index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    vpn_sync_needed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    vpn_sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    vpn_last_error: Mapped[str | None] = mapped_column(String(255))
    notified_24h_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notified_final_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notified_expired_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Device(Base):
    """One VPN client per device: each gets its own panel user and subscription link."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(16), default=DeviceStatus.PENDING, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    external_username: Mapped[str] = mapped_column(String(64), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(128))
    sync_needed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BonusEntry(Base):
    """Ledger of bonus days. unique_key makes each reward (e.g. referral per invitee and kind) one-shot."""

    __tablename__ = "bonus_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    days: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255), default="")
    unique_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    related_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class BonusSource:
    REFERRAL_TRIAL = "referral_trial"
    REFERRAL_PAYMENT = "referral_payment"
    PROMO = "promo"
    ADMIN = "admin"
    CORRECTION = "correction"

    REFERRAL = (REFERRAL_TRIAL, REFERRAL_PAYMENT)


class Staff(Base):
    __tablename__ = "staff"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class VPNNode(Base):
    __tablename__ = "vpn_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    address: Mapped[str] = mapped_column(String(255), default="")
    country: Mapped[str] = mapped_column(String(8), default="")
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    users_online: Mapped[int] = mapped_column(Integer, default=0)
    status_message: Mapped[str | None] = mapped_column(String(255))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class VPNError(Base):
    __tablename__ = "vpn_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operation: Mapped[str] = mapped_column(String(48))
    error: Mapped[str] = mapped_column(String(255))
    device_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    duration_days: Mapped[int] = mapped_column(Integer)  # 0 for lifetime
    device_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    price: Mapped[int] = mapped_column(Integer, default=0)  # minor units
    currency: Mapped[str] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    plan_code: Mapped[str] = mapped_column(String(32))
    plan_title: Mapped[str] = mapped_column(String(128))
    duration_days: Mapped[int] = mapped_column(Integer)
    device_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_lifetime: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    amount_original: Mapped[int] = mapped_column(Integer)
    discount: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8))
    promo_activation_id: Mapped[int | None] = mapped_column(ForeignKey("promo_activations.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.CREATED, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PaymentEvent(Base):
    """Every payment notification; UNIQUE(provider, event_id) makes processing idempotent."""

    __tablename__ = "payment_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(255))
    order_id: Mapped[str | None] = mapped_column(String(36), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[str] = mapped_column(String(32), default="received")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    max_activations: Mapped[int | None] = mapped_column(Integer)
    activations_count: Mapped[int] = mapped_column(Integer, default=0)
    allowed_group: Mapped[str | None] = mapped_column(String(32))
    allowed_plans: Mapped[str | None] = mapped_column(String(255))  # comma-separated plan codes; discounts only
    new_users_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PromoActivation(Base):
    __tablename__ = "promo_activations"
    __table_args__ = (UniqueConstraint("promo_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    from_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

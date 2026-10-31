"""SQLAlchemy models. Telegram ID is the primary user identifier. All timestamps are UTC."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

TS = DateTime(timezone=True)


def _now_col(**kw: Any) -> Mapped[datetime]:
    return mapped_column(TS, server_default=func.now(), nullable=False, **kw)


class SubStatus:
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    FAILED = "failed"
    ALL = (PENDING, ACTIVE, EXPIRED, SUSPENDED, REVOKED, FAILED)
    CURRENT = (PENDING, ACTIVE, EXPIRED, SUSPENDED)


class DevStatus:
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKING = "revoking"
    REVOKED = "revoked"
    FAILED = "failed"
    ALL = (PENDING, ACTIVE, EXPIRED, SUSPENDED, REVOKING, REVOKED, FAILED)
    # Statuses that occupy a slot of the plan's device limit.
    OCCUPYING = (PENDING, ACTIVE, EXPIRED, SUSPENDED, REVOKING)


class OrderStatus:
    CREATED = "created"
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"
    FAILED = "failed"
    REFUNDED = "refunded"
    ALL = (CREATED, PENDING, PAID, CANCELED, FAILED, REFUNDED)


class Fulfillment:
    NONE = "none"
    PENDING = "pending"
    DONE = "done"
    VPN_FAILED = "vpn_failed"
    ALL = (NONE, PENDING, DONE, VPN_FAILED)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # Public, non-sequential SpirittVPN ID (e.g. SV-8K4M2Q). Never changes once assigned.
    public_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # Version of the accepted documents (privacy policy + user agreement); NULL = not accepted yet.
    terms_version: Mapped[str | None] = mapped_column(String(32))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    # SpirCoin balance. Changed only together with a CoinTransaction row (services/coins.py).
    coin_balance: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    __table_args__ = (
        Index("ix_users_created_at", "created_at"),
        CheckConstraint("coin_balance >= 0", name="coin_balance_nonnegative"),
    )

    @property
    def display_name(self) -> str:
        return self.first_name or (f"@{self.username}" if self.username else str(self.id))


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), server_default="RUB", nullable=False)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    # Price in SpirCoin; NULL = not purchasable with coins.
    price_coins: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())

    __table_args__ = (
        CheckConstraint("price_minor > 0", name="price_positive"),
        CheckConstraint("price_coins IS NULL OR price_coins > 0", name="price_coins_positive"),
        CheckConstraint("device_limit BETWEEN 1 AND 50", name="device_limit_range"),
        CheckConstraint(
            "(is_lifetime AND duration_days IS NULL) OR (NOT is_lifetime AND duration_days > 0)",
            name="duration_consistent",
        ),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, default=uuid.uuid4, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False)
    # Snapshot of the plan at order time: later price/limit changes don't affect existing orders.
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    plan_title: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_days: Mapped[int | None] = mapped_column(Integer)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default=OrderStatus.CREATED, nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(String(16), server_default=Fulfillment.NONE, nullable=False)
    fulfillment_error: Mapped[str | None] = mapped_column(Text)
    # Promo discount already subtracted from amount_minor (snapshot).
    discount_minor: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    promo_activation_id: Mapped[int | None] = mapped_column(ForeignKey("promo_activations.id", ondelete="SET NULL"))
    # Snapshots taken when the order is created (never back-filled for older orders).
    promo_code: Mapped[str | None] = mapped_column(String(32))
    referrer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(TS)
    fulfilled_at: Mapped[datetime | None] = mapped_column(TS)

    payments: Mapped[list[Payment]] = relationship(back_populates="order", lazy="selectin")
    promo_activation: Mapped["PromoActivation | None"] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(_in("status", OrderStatus.ALL), name="status_valid"),
        CheckConstraint(_in("fulfillment_status", Fulfillment.ALL), name="fulfillment_valid"),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_referrer", "referrer_id", "status", postgresql_where=text("referrer_id IS NOT NULL")),
        Index("ix_orders_promo_code", "promo_code", "status", postgresql_where=text("promo_code IS NOT NULL")),
    )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    confirmation_url: Mapped[str | None] = mapped_column(Text)
    is_test: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)

    order: Mapped[Order] = relationship(back_populates="payments")

    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_pid"),
        Index("ix_payments_order", "order_id"),
        Index("ix_payments_status", "status"),
    )


class PaymentEvent(Base):
    """Every processed provider event, for idempotency and audit."""

    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # webhook | poll | manual
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", "event", name="uq_payment_events_once"),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # trial | payment | admin | bonus
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    suspended_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("status", SubStatus.ALL), name="status_valid"),
        CheckConstraint("device_limit BETWEEN 1 AND 50", name="device_limit_range"),
        # Lifetime never has an end date; limited subscriptions always do once started.
        CheckConstraint(
            "(is_lifetime AND expires_at IS NULL) OR (NOT is_lifetime AND (expires_at IS NOT NULL OR status IN ('pending','failed')))",
            name="expiry_consistent",
        ),
        # At most one current subscription per user.
        Index(
            "uq_subscriptions_current_user",
            "user_id",
            unique=True,
            postgresql_where=text(_in("status", SubStatus.CURRENT)),
        ),
        Index("ix_subscriptions_status_expires", "status", "expires_at"),
    )


class SubscriptionEvent(Base):
    """History of every subscription change. order_id uniqueness prevents applying a payment twice."""

    __tablename__ = "subscription_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    old_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str | None] = mapped_column(String(16))
    old_expires_at: Mapped[datetime | None] = mapped_column(TS)
    new_expires_at: Mapped[datetime | None] = mapped_column(TS)
    days: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)  # system | user | admin:<id>
    comment: Mapped[str | None] = mapped_column(Text)  # internal (admin reason)
    public_note: Mapped[str | None] = mapped_column(String(200))  # shown to the user
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        Index("uq_subscription_events_order", "order_id", unique=True, postgresql_where=text("order_id IS NOT NULL")),
        Index("ix_subscription_events_sub", "subscription_id", "created_at"),
    )


class TrialActivation(Base):
    """One row per user, ever. Only status=active means the trial is used."""

    __tablename__ = "trial_activations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | active | failed
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    activated_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (CheckConstraint("status IN ('pending','active','failed')", name="status_valid"),)


class VpnNode(Base):
    __tablename__ = "vpn_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_health_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    remote_clients: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now_col()

    @property
    def flag(self) -> str:
        cc = self.country_code.upper()
        if len(cc) != 2 or not cc.isalpha() or cc == "ZZ":  # ZZ marks the virtual "all locations" node
            return "🌐"
        return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("vpn_nodes.id", ondelete="RESTRICT"), nullable=False)
    client_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_uuid: Mapped[str | None] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(16), server_default="vless", nullable=False)
    label: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    subscription_url: Mapped[str | None] = mapped_column(Text)
    connection_link: Mapped[str | None] = mapped_column(Text)
    node_expires_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)
    last_synced_at: Mapped[datetime | None] = mapped_column(TS)

    node: Mapped[VpnNode] = relationship(lazy="joined", innerjoin=True)

    __table_args__ = (
        CheckConstraint(_in("status", DevStatus.ALL), name="status_valid"),
        Index("ix_devices_user_status", "user_id", "status"),
        Index("ix_devices_subscription", "subscription_id"),
    )


class VpnOperation(Base):
    """Journal of every call to VPN infrastructure; pending rows are retried by the worker."""

    __tablename__ = "vpn_operations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("vpn_nodes.id", ondelete="SET NULL"))
    op_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | success | failed
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(TS)
    actor: Mapped[str] = mapped_column(String(32), server_default="system", nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','success','failed')", name="status_valid"),
        Index(
            "uq_vpn_operations_pending",
            "device_id",
            "op_type",
            unique=True,
            postgresql_where=text("status = 'pending' AND device_id IS NOT NULL"),
        ),
        Index("ix_vpn_operations_status_retry", "status", "next_retry_at"),
        Index("ix_vpn_operations_created", "created_at"),
    )


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    referred_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False)
    created_at: Mapped[datetime] = _now_col()
    trial_activated_at: Mapped[datetime | None] = mapped_column(TS)
    first_paid_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("referrer_id <> referred_id", name="no_self_referral"),
        Index("ix_referrals_referrer", "referrer_id", "created_at"),
    )


class ReferralReward(Base):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referral_id: Mapped[int] = mapped_column(ForeignKey("referrals.id", ondelete="RESTRICT"), nullable=False)
    referrer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # trial_activated | first_payment
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="granted", nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now_col()
    reversed_at: Mapped[datetime | None] = mapped_column(TS)
    reversed_by: Mapped[int | None] = mapped_column(BigInteger)
    reverse_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("referral_id", "event_type", name="uq_referral_rewards_once"),
        CheckConstraint("event_type IN ('trial_activated','first_payment')", name="event_valid"),
        CheckConstraint("status IN ('granted','reversed')", name="status_valid"),
        CheckConstraint("days > 0", name="days_positive"),
        Index("ix_referral_rewards_referrer", "referrer_id"),
    )


class BonusTransaction(Base):
    """Ledger of bonus days. Balance = SUM(days)."""

    __tablename__ = "bonus_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    referral_reward_id: Mapped[int | None] = mapped_column(ForeignKey("referral_rewards.id", ondelete="RESTRICT"))
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        CheckConstraint("days <> 0", name="days_nonzero"),
        CheckConstraint(
            "kind IN ('referral_trial','referral_payment','admin_adjust','applied','reversal')", name="kind_valid"
        ),
        Index(
            "uq_bonus_transactions_reward_kind",
            "referral_reward_id",
            "kind",
            unique=True,
            postgresql_where=text("referral_reward_id IS NOT NULL"),
        ),
        Index("ix_bonus_transactions_user", "user_id", "created_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # bot | web
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Any | None] = mapped_column(JSONB)
    new_value: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        CheckConstraint("length(btrim(reason)) >= 3", name="reason_required"),
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())


class AdminLoginCode(Base):
    __tablename__ = "admin_login_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (Index("ix_admin_login_codes_admin", "admin_id", "created_at"),)


class TermsConsent(Base):
    """Append-only log of document acceptances (one row per user per version)."""

    __tablename__ = "terms_consents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    privacy_url: Mapped[str] = mapped_column(Text, nullable=False)
    terms_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # bot | miniapp
    accepted_at: Mapped[datetime] = _now_col()

    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_terms_consents_user_version"),)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # stored upper-case
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # percent | fixed | days
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # % | kopecks | days
    max_activations: Mapped[int | None] = mapped_column(Integer)
    activations: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        CheckConstraint("kind IN ('percent','fixed','days')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'percent' AND value BETWEEN 1 AND 90) OR (kind = 'fixed' AND value BETWEEN 100 AND 10000000) OR (kind = 'days' AND value BETWEEN 1 AND 365)",
            name="value_range",
        ),
        CheckConstraint("max_activations IS NULL OR max_activations >= 1", name="max_positive"),
        # The counter can never exceed the limit, even under concurrency.
        CheckConstraint("max_activations IS NULL OR activations <= max_activations", name="within_limit"),
    )


class PromoActivation(Base):
    __tablename__ = "promo_activations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    # reserved: discount waiting for an order; used: discount paid; applied: free days granted
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now_col()
    used_at: Mapped[datetime | None] = mapped_column(TS)

    promo: Mapped[PromoCode] = relationship(lazy="selectin")

    __table_args__ = (
        # Each user can use a given promo code once.
        UniqueConstraint("promo_id", "user_id", name="uq_promo_activations_once"),
        CheckConstraint("status IN ('reserved','used','applied')", name="status_valid"),
        Index("ix_promo_activations_user", "user_id", "status"),
    )


class AdminRole(Base):
    """DB-managed admins. OWNER_TELEGRAM_ID / ADMIN_IDS from the environment are admins in addition to these."""

    __tablename__ = "admin_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # owner | admin | moderator
    granted_by: Mapped[int | None] = mapped_column(BigInteger)
    granted_at: Mapped[datetime] = _now_col()

    __table_args__ = (CheckConstraint("role IN ('owner','admin','moderator')", name="role_valid"),)


class TicketStatus:
    NEW = "new"
    IN_PROGRESS = "in_progress"
    WAITING_USER = "waiting_user"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ALL = (NEW, IN_PROGRESS, WAITING_USER, RESOLVED, CLOSED)
    OPEN = (NEW, IN_PROGRESS, WAITING_USER)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default=TicketStatus.NEW, nullable=False)
    assigned_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint(_in("status", TicketStatus.ALL), name="status_valid"),
        CheckConstraint("category IN ('payment','connection','subscription','devices','app_error','other')", name="category_valid"),
        Index("ix_support_tickets_user", "user_id", "created_at"),
        Index("ix_support_tickets_status", "status", "updated_at"),
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    author_type: Mapped[str] = mapped_column(String(8), nullable=False)  # user | admin | system
    author_id: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    attachment_file_id: Mapped[str | None] = mapped_column(String(256))  # Telegram file_id
    attachment_kind: Mapped[str | None] = mapped_column(String(16))  # photo | document
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        CheckConstraint("author_type IN ('user','admin','system')", name="author_valid"),
        # Users can never author internal notes.
        CheckConstraint("NOT (is_internal AND author_type = 'user')", name="no_internal_from_user"),
        Index("ix_support_messages_ticket", "ticket_id", "created_at"),
    )



class CoinTransaction(Base):
    """SpirCoin ledger. users.coin_balance always equals the last balance_after of the user."""

    __tablename__ = "coin_transactions"

    KINDS = ("task_reward", "purchase", "admin_grant", "admin_debit", "refund")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"))
    # Client idempotency key (purchases): a retried request returns the first result instead of paying twice.
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        CheckConstraint("balance_after >= 0", name="balance_nonnegative"),
        CheckConstraint(_in("kind", KINDS), name="kind_valid"),
        Index("uq_coin_transactions_idem", "user_id", "idempotency_key", unique=True, postgresql_where=text("idempotency_key IS NOT NULL")),
        # One reward per task per user, enforced by the database as well.
        Index("uq_coin_transactions_task_reward", "user_id", "task_id", unique=True, postgresql_where=text("kind = 'task_reward'")),
        Index("ix_coin_transactions_user", "user_id", "created_at"),
        Index("ix_coin_transactions_created", "created_at"),
    )


class Task(Base):
    """A SpirCoin task (e.g. subscribe to a channel). Archived tasks keep their history."""

    __tablename__ = "tasks"

    VERIFY = ("telegram", "manual")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    reward: Mapped[int] = mapped_column(Integer, nullable=False)
    verify: Mapped[str] = mapped_column(String(16), nullable=False)
    # Chat for verify=telegram (e.g. @spirit_vpn_news). The bot must be able to read its members.
    chat_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TS)
    budget_total: Mapped[int | None] = mapped_column(BigInteger)
    issued_total: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    max_completions: Mapped[int | None] = mapped_column(Integer)
    completions: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    last_check_error: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())

    __table_args__ = (
        CheckConstraint("reward > 0", name="reward_positive"),
        CheckConstraint(_in("verify", VERIFY), name="verify_valid"),
        CheckConstraint("budget_total IS NULL OR issued_total <= budget_total", name="within_budget"),
        CheckConstraint("max_completions IS NULL OR completions <= max_completions", name="within_completions"),
    )


class TaskClaim(Base):
    """A user's attempt at a task. One row per (task, user); rewarded rows are final."""

    __tablename__ = "task_claims"

    STATUSES = ("pending", "rewarded", "rejected")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    created_at: Mapped[datetime] = _now_col()
    decided_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_claims_once"),
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        Index("ix_task_claims_status", "status", "created_at"),
    )


class ReferralVisit(Base):
    """Someone opened the bot with a referral link. One row per (referrer, visitor); repeats only bump the counter."""

    __tablename__ = "referral_visits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    visitor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # registered | existing
    visits: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    first_at: Mapped[datetime] = _now_col()
    last_at: Mapped[datetime] = _now_col()

    __table_args__ = (
        UniqueConstraint("referrer_id", "visitor_id", name="uq_referral_visits_pair"),
        CheckConstraint("referrer_id <> visitor_id", name="no_self_visit"),
        CheckConstraint("outcome IN ('registered','existing')", name="outcome_valid"),
        Index("ix_referral_visits_referrer", "referrer_id", "first_at"),
    )


class TrialCode(Base):
    """Secret code that grants a one-time test subscription (one per account across all codes)."""

    __tablename__ = "trial_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # stored upper-case
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    days: Mapped[int] = mapped_column(Integer, server_default="5", nullable=False)
    max_activations: Mapped[int | None] = mapped_column(Integer)
    activations: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(TS)
    ends_at: Mapped[datetime | None] = mapped_column(TS)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = _now_col(onupdate=func.now())

    __table_args__ = (
        CheckConstraint("days BETWEEN 1 AND 30", name="days_range"),
        CheckConstraint("max_activations IS NULL OR max_activations >= 1", name="max_positive"),
        # The counter can never pass the limit, even when two users race for the last activation.
        CheckConstraint("max_activations IS NULL OR activations <= max_activations", name="within_limit"),
        CheckConstraint("activations >= 0", name="activations_nonnegative"),
    )


class TrialCodeActivation(Base):
    """At most one row per user (unique user_id): a test subscription by code is granted once per account."""

    __tablename__ = "trial_code_activations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code_id: Mapped[int] = mapped_column(ForeignKey("trial_codes.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | active | failed
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    activated_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("status IN ('pending','active','failed')", name="status_valid"),
        Index("ix_trial_code_activations_code", "code_id", "created_at"),
    )

__all__ = [
    "TermsConsent",
    "PromoCode",
    "PromoActivation",
    "AdminRole",
    "TicketStatus",
    "SupportTicket",
    "SupportMessage",
    "Base",
    "User",
    "Plan",
    "Order",
    "Payment",
    "PaymentEvent",
    "Subscription",
    "SubscriptionEvent",
    "TrialActivation",
    "VpnNode",
    "Device",
    "VpnOperation",
    "Referral",
    "ReferralReward",
    "BonusTransaction",
    "AuditLog",
    "AppSetting",
    "AdminLoginCode",
    "SubStatus",
    "DevStatus",
    "OrderStatus",
    "Fulfillment",
    "CoinTransaction",
    "Task",
    "TaskClaim",
    "ReferralVisit",
    "TrialCode",
    "TrialCodeActivation",
]


class Broadcast(Base):
    """Admin message to one user or to everyone; also maintenance notices. Doubles as the send journal."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # single | all | maintenance_down | maintenance_up
    target_user_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="queued", nullable=False)
    total: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    sent: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    failed: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    blocked: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    # Last processed user id: a restarted sender resumes after it instead of messaging people twice.
    cursor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _now_col()
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("kind IN ('single','all','maintenance_down','maintenance_up')", name="kind_valid"),
        CheckConstraint("status IN ('queued','running','done','cancelled','failed')", name="status_valid"),
        Index("ix_broadcasts_status", "status", "id"),
    )

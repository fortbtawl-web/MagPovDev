"""Referral visits, order referrer/promo snapshots, trial codes

Additive only. Existing orders keep NULL referrer_id/promo_code: history is never attributed retroactively.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("promo_code", sa.String(length=32), nullable=True))
    op.add_column("orders", sa.Column("referrer_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f("fk_orders_referrer_id_users"), "orders", "users", ["referrer_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_orders_referrer", "orders", ["referrer_id", "status"], postgresql_where=sa.text("referrer_id IS NOT NULL"))
    op.create_index("ix_orders_promo_code", "orders", ["promo_code", "status"], postgresql_where=sa.text("promo_code IS NOT NULL"))

    op.create_table(
        "referral_visits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("referrer_id", sa.BigInteger(), nullable=False),
        sa.Column("visitor_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("visits", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("referrer_id <> visitor_id", name=op.f("ck_referral_visits_no_self_visit")),
        sa.CheckConstraint("outcome IN ('registered','existing')", name=op.f("ck_referral_visits_outcome_valid")),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], name=op.f("fk_referral_visits_referrer_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["visitor_id"], ["users.id"], name=op.f("fk_referral_visits_visitor_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referral_visits")),
        sa.UniqueConstraint("referrer_id", "visitor_id", name="uq_referral_visits_pair"),
    )
    op.create_index("ix_referral_visits_referrer", "referral_visits", ["referrer_id", "first_at"])

    op.create_table(
        "trial_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("days", sa.Integer(), server_default="5", nullable=False),
        sa.Column("max_activations", sa.Integer(), nullable=True),
        sa.Column("activations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("days BETWEEN 1 AND 30", name=op.f("ck_trial_codes_days_range")),
        sa.CheckConstraint("max_activations IS NULL OR max_activations >= 1", name=op.f("ck_trial_codes_max_positive")),
        sa.CheckConstraint("max_activations IS NULL OR activations <= max_activations", name=op.f("ck_trial_codes_within_limit")),
        sa.CheckConstraint("activations >= 0", name=op.f("ck_trial_codes_activations_nonnegative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_trial_codes_code")),
    )
    op.create_table(
        "trial_code_activations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','active','failed')", name=op.f("ck_trial_code_activations_status_valid")),
        sa.ForeignKeyConstraint(["code_id"], ["trial_codes.id"], name=op.f("fk_trial_code_activations_code_id_trial_codes"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trial_code_activations_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], name=op.f("fk_trial_code_activations_subscription_id_subscriptions"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_code_activations")),
        sa.UniqueConstraint("user_id", name=op.f("uq_trial_code_activations_user_id")),
    )
    op.create_index("ix_trial_code_activations_code", "trial_code_activations", ["code_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_trial_code_activations_code", table_name="trial_code_activations")
    op.drop_table("trial_code_activations")
    op.drop_table("trial_codes")
    op.drop_index("ix_referral_visits_referrer", table_name="referral_visits")
    op.drop_table("referral_visits")
    op.drop_index("ix_orders_promo_code", table_name="orders")
    op.drop_index("ix_orders_referrer", table_name="orders")
    op.drop_constraint(op.f("fk_orders_referrer_id_users"), "orders", type_="foreignkey")
    op.drop_column("orders", "referrer_id")
    op.drop_column("orders", "promo_code")

"""SpirCoin balance and ledger, tasks, coin prices, moderator role

Additive only: new tables/columns and a wider role check. No existing data is changed except
filling price_coins for the five known plan codes (only where it is still NULL).

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

PLAN_COINS = {"dev1_30": 1000, "dev2_30": 2000, "m3": 2500, "y1": 11500, "lifetime": 500000}
SEED_TASKS = [
    # code, title, description, url, reward, chat_id, sort
    ("spirit_news", "Канал SpiritVPN", "Подпишитесь на официальный канал: новости, статус серверов и акции.",
     "https://t.me/spirit_vpn_news", 1, "@spirit_vpn_news", 10),
    ("bowierp", "Канал BowieRP", "Подпишитесь на канал BowieRP.", "https://t.me/BowieRP", 50, "@BowieRP", 20),
    ("azbukov_rp", "Канал azbukov_rp", "Подпишитесь на канал azbukov_rp.", "https://t.me/azbukov_rp", 10, "@azbukov_rp", 30),
]


def upgrade() -> None:
    op.add_column("users", sa.Column("coin_balance", sa.BigInteger(), server_default="0", nullable=False))
    op.create_check_constraint("coin_balance_nonnegative", "users", "coin_balance >= 0")

    op.add_column("plans", sa.Column("price_coins", sa.Integer(), nullable=True))
    op.create_check_constraint("price_coins_positive", "plans", "price_coins IS NULL OR price_coins > 0")

    op.drop_constraint(op.f("ck_admin_roles_role_valid"), "admin_roles", type_="check")
    op.create_check_constraint("role_valid", "admin_roles", "role IN ('owner','admin','moderator')")

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("url", sa.String(length=300), nullable=False),
        sa.Column("reward", sa.Integer(), nullable=False),
        sa.Column("verify", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_total", sa.BigInteger(), nullable=True),
        sa.Column("issued_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("max_completions", sa.Integer(), nullable=True),
        sa.Column("completions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_check_error", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("reward > 0", name=op.f("ck_tasks_reward_positive")),
        sa.CheckConstraint("verify IN ('telegram','manual')", name=op.f("ck_tasks_verify_valid")),
        sa.CheckConstraint("verify <> 'telegram' OR chat_id IS NOT NULL", name=op.f("ck_tasks_telegram_needs_chat")),
        sa.CheckConstraint("budget_total IS NULL OR issued_total <= budget_total", name=op.f("ck_tasks_within_budget")),
        sa.CheckConstraint("max_completions IS NULL OR completions <= max_completions", name=op.f("ck_tasks_within_completions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("code", name=op.f("uq_tasks_code")),
    )
    op.create_table(
        "coin_transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("balance_after", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_coin_transactions_amount_nonzero")),
        sa.CheckConstraint("balance_after >= 0", name=op.f("ck_coin_transactions_balance_nonnegative")),
        sa.CheckConstraint(
            "kind IN ('task_reward','purchase','admin_grant','admin_debit','refund')", name=op.f("ck_coin_transactions_kind_valid")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_coin_transactions_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_coin_transactions_order_id_orders"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_coin_transactions_task_id_tasks"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coin_transactions")),
    )
    op.create_index("uq_coin_transactions_idem", "coin_transactions", ["user_id", "idempotency_key"], unique=True,
                    postgresql_where=sa.text("idempotency_key IS NOT NULL"))
    op.create_index("uq_coin_transactions_task_reward", "coin_transactions", ["user_id", "task_id"], unique=True,
                    postgresql_where=sa.text("kind = 'task_reward'"))
    op.create_index("ix_coin_transactions_user", "coin_transactions", ["user_id", "created_at"])
    op.create_index("ix_coin_transactions_created", "coin_transactions", ["created_at"])
    op.create_table(
        "task_claims",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','rewarded','rejected')", name=op.f("ck_task_claims_status_valid")),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_task_claims_task_id_tasks"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_task_claims_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_claims")),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_claims_once"),
    )
    op.create_index("ix_task_claims_status", "task_claims", ["status", "created_at"])

    plans = sa.table("plans", sa.column("code", sa.String), sa.column("price_coins", sa.Integer))
    for code, coins in PLAN_COINS.items():
        op.execute(plans.update().where(plans.c.code == code).where(plans.c.price_coins.is_(None)).values(price_coins=coins))
    tasks = sa.table(
        "tasks", sa.column("code", sa.String), sa.column("title", sa.String), sa.column("description", sa.Text),
        sa.column("url", sa.String), sa.column("reward", sa.Integer), sa.column("verify", sa.String),
        sa.column("chat_id", sa.String), sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(tasks, [
        {"code": c, "title": t, "description": d, "url": u, "reward": r, "verify": "telegram", "chat_id": ch, "sort_order": so}
        for c, t, d, u, r, ch, so in SEED_TASKS
    ])


def downgrade() -> None:
    op.drop_index("ix_task_claims_status", table_name="task_claims")
    op.drop_table("task_claims")
    op.drop_index("ix_coin_transactions_created", table_name="coin_transactions")
    op.drop_index("ix_coin_transactions_user", table_name="coin_transactions")
    op.drop_index("uq_coin_transactions_task_reward", table_name="coin_transactions")
    op.drop_index("uq_coin_transactions_idem", table_name="coin_transactions")
    op.drop_table("coin_transactions")
    op.drop_table("tasks")
    op.execute("DELETE FROM admin_roles WHERE role = 'moderator'")
    op.drop_constraint(op.f("ck_admin_roles_role_valid"), "admin_roles", type_="check")
    op.create_check_constraint("role_valid", "admin_roles", "role IN ('owner','admin')")
    op.drop_constraint(op.f("ck_plans_price_coins_positive"), "plans", type_="check")
    op.drop_column("plans", "price_coins")
    op.drop_constraint(op.f("ck_users_coin_balance_nonnegative"), "users", type_="check")
    op.drop_column("users", "coin_balance")

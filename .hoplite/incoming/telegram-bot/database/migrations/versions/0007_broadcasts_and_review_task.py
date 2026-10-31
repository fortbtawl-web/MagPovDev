"""Broadcasts (admin messages + maintenance notices); retire the "review" task

Additive: a new broadcasts table. Data: tasks whose title mentions «отзыв» are archived and deactivated
(rows, claims and coin history are kept; pending claims for them are rejected so no reward is ever issued).

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

REJECT_PENDING_REVIEW_CLAIMS = """
UPDATE task_claims SET status = 'rejected', decided_at = now(), reason = 'Задание снято с публикации'
 WHERE status = 'pending'
   AND task_id IN (SELECT id FROM tasks WHERE title ILIKE '%отзыв%' AND archived_at IS NULL)
"""
ARCHIVE_REVIEW_TASKS = "UPDATE tasks SET is_active = false, archived_at = now() WHERE title ILIKE '%отзыв%' AND archived_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("blocked", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cursor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('single','all','maintenance_down','maintenance_up')", name=op.f("ck_broadcasts_kind_valid")),
        sa.CheckConstraint("status IN ('queued','running','done','cancelled','failed')", name=op.f("ck_broadcasts_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcasts")),
    )
    op.create_index("ix_broadcasts_status", "broadcasts", ["status", "id"])

    op.execute(REJECT_PENDING_REVIEW_CLAIMS)
    op.execute(ARCHIVE_REVIEW_TASKS)

def downgrade() -> None:
    # The archived task stays archived on downgrade (restoring it would re-open rewards).
    op.drop_index("ix_broadcasts_status", table_name="broadcasts")
    op.drop_table("broadcasts")

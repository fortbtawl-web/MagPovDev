"""Link tasks without a channel; co-owner 6964704172

* tasks: the channel username is no longer required for "telegram" (link) tasks — they reward the click on the
  task button, membership is not checked. Existing rows are untouched.
* admin_roles: grants owner to Telegram ID 6964704172, only if that user exists AND his SpirittVPN ID is SV-J5MJRH
  (the ID the owner provided), so a wrong account can never be promoted. Existing roles are kept.
  The same person can also be listed in OWNER_IDS in .env (works even before he opens the bot).

Revision ID: 0008
Revises: 0007
"""
from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

GRANT_OWNER = """
INSERT INTO admin_roles (user_id, role, granted_by)
SELECT id, 'owner', NULL FROM users WHERE id = 6964704172 AND public_id = 'SV-J5MJRH'
ON CONFLICT (user_id) DO UPDATE SET role = 'owner'
"""


def upgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_telegram_needs_chat")
    op.execute(GRANT_OWNER)


def downgrade() -> None:
    op.execute("UPDATE tasks SET verify = 'manual' WHERE verify = 'telegram' AND chat_id IS NULL")
    op.execute("ALTER TABLE tasks ADD CONSTRAINT ck_tasks_telegram_needs_chat CHECK (verify <> 'telegram' OR chat_id IS NOT NULL)")

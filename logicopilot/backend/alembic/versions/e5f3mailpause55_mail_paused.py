"""users.mail_paused - a per-mailbox on/off switch for the email poller, independent of the
system-wide email_pull_paused flag in system_settings.py. Super Admin's Settings page lists
every operator's connected mailbox (Gmail, Zoho, ...) with its own toggle; _operator_mailboxes
(app/core/email_puller.py) skips any row with this set, the same way the whole-system pause
already skips every mailbox.

Revision ID: e5f3mailpause55
Revises: d4a2reextractflag54
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f3mailpause55"
down_revision = "d4a2reextractflag54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mail_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "mail_paused")

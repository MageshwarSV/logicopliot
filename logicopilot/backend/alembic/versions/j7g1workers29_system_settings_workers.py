"""system settings workers — system_settings.email_poll_workers

How many mailboxes the email poller reads at once, live-editable from Settings (Super Admin),
capped at the server's own CPU count. Defaults to 1 - safe on any server size.

Revision ID: j7g1workers29
Revises: i6f0aikey28
"""
from alembic import op
import sqlalchemy as sa

revision = "j7g1workers29"
down_revision = "i6f0aikey28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("email_poll_workers", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "email_poll_workers")

"""system settings cycle status — system_settings.last_email_cycle_status

A JSON snapshot of the email poller's last cycle, moved from a plain module-level dict
into the database because the backend now runs as several uvicorn --workers processes and
only one of them (the singleton-lock holder) ever polls — any other worker's in-memory
copy would sit empty forever even while polling genuinely is happening.

Revision ID: k8h2cycle30
Revises: j7g1workers29
"""
from alembic import op
import sqlalchemy as sa

revision = "k8h2cycle30"
down_revision = "j7g1workers29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("last_email_cycle_status", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "last_email_cycle_status")

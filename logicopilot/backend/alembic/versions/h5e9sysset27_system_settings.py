"""system settings — email_pull_paused, extraction_paused

A single global row a Super Admin can flip live (no restart) to stop the email poller and/or
document extraction from making any further AI/IMAP calls, while the rest of the backend
keeps running normally. See app/models/system_setting.py.

Revision ID: h5e9sysset27
Revises: g4d8dup26
"""
from alembic import op
import sqlalchemy as sa

revision = "h5e9sysset27"
down_revision = "g4d8dup26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email_pull_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extraction_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_settings")

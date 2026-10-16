"""Add system_settings.erp_script_workers - bounds how many real ERP submissions
(browser automation) run at once, mirroring email_poll_workers.

Revision ID: a4b2erp75
Revises: z3a1gk274
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "a4b2erp75"
down_revision = "z3a1gk274"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_settings",
        sa.Column("erp_script_workers", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "erp_script_workers")

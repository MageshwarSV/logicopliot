"""Add tenants.gk2_required_by_mode and jobs.gk2_skipped - lets a Tenant Admin make GK2
review optional per shipment type, so GK1's own approval can go straight to ERP.

Revision ID: z3a1gk274
Revises: y2z0ple73
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "z3a1gk274"
down_revision = "y2z0ple73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("gk2_required_by_mode", sa.JSON(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("gk2_skipped", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("jobs", "gk2_skipped")
    op.drop_column("tenants", "gk2_required_by_mode")

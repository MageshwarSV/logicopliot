"""Add billing_start_date (tenant_billing_configs) and platform fee proration columns (invoices)

Revision ID: x1y9bsd72
Revises: w0x8inv71
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "x1y9bsd72"
down_revision = "w0x8inv71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_configs",
        sa.Column("billing_start_date", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "invoices",
        sa.Column("platform_fee_billed_days", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "invoices",
        sa.Column("platform_fee_month_days", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.drop_column("invoices", "platform_fee_month_days")
    op.drop_column("invoices", "platform_fee_billed_days")
    op.drop_column("tenant_billing_configs", "billing_start_date")

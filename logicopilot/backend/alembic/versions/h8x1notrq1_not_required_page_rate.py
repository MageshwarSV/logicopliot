"""Add tenant_billing_configs.not_required_page_rate and invoices.not_required_rate/_pages/
_amount - a page never matched to any document ("Not Required", previously labelled
"Unclassified") is now billed at a flat per-tenant rate INSTEAD of the Tier 1/2 rate, not on
top of it. See app/core/billing.py's calculate_bill for the arithmetic.

Additive, nullable-safe defaults:
- not_required_page_rate defaults to 1.00 (₹1/page) for every tenant, including existing
  rows with no explicit value yet - the standard published rate until a Super Admin changes
  it per tenant.
- invoices.not_required_rate/_pages/_amount default to 0/0/0.0 on every already-raised
  invoice, which is correct: those pages were billed at the Tier 1/2 rate like any other page
  at the time, nothing to retroactively re-price.

Revision ID: h8x1notrq1
Revises: g7w0wrkf1
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

revision = "h8x1notrq1"
down_revision = "g7w0wrkf1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_configs",
        sa.Column("not_required_page_rate", sa.Float(), nullable=False, server_default="1.00"),
    )
    op.add_column(
        "invoices",
        sa.Column("not_required_rate", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "invoices",
        sa.Column("not_required_pages", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "invoices",
        sa.Column("not_required_amount", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("invoices", "not_required_amount")
    op.drop_column("invoices", "not_required_pages")
    op.drop_column("invoices", "not_required_rate")
    op.drop_column("tenant_billing_configs", "not_required_page_rate")

"""manual_invoices - one-off invoices written by hand (draft -> issued -> paid / cancelled).

Revision ID: i3manualinv01
Revises: i2erpfirst01
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "i3manualinv01"
down_revision = "i2erpfirst01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_invoices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("invoice_number", sa.String(40), nullable=True, unique=True),
        sa.Column("financial_year", sa.String(7), nullable=True),
        sa.Column("fy_sequence", sa.Integer(), nullable=True),
        sa.Column("invoice_date", sa.String(10), nullable=False),
        sa.Column("due_date", sa.String(10), nullable=False),
        sa.Column("period_label", sa.String(80), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("gst_percent", sa.Float(), nullable=False, server_default="18"),
        sa.Column("subtotal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gst_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tax_mode", sa.String(10), nullable=True),
        sa.Column("cgst_amount", sa.Float(), nullable=True),
        sa.Column("sgst_amount", sa.Float(), nullable=True),
        sa.Column("igst_amount", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("seller", sa.JSON(), nullable=True),
        sa.Column("buyer", sa.JSON(), nullable=True),
        sa.Column("issued_at", sa.String(30), nullable=True),
        sa.Column("paid_at", sa.String(30), nullable=True),
        sa.Column("cancelled_at", sa.String(30), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_manual_invoices_tenant_id", "manual_invoices", ["tenant_id"])
    op.create_index("ix_manual_invoices_financial_year", "manual_invoices", ["financial_year"])


def downgrade() -> None:
    op.drop_table("manual_invoices")

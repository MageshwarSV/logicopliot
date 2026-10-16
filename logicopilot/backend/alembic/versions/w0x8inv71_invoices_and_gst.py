"""Add invoices table and gst_percent on tenant_billing_configs

Revision ID: w0x8inv71
Revises: v9w7bill70
Create Date: 2026-10-16
"""

from alembic import op
import sqlalchemy as sa

revision = "w0x8inv71"
down_revision = "v9w7bill70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_billing_configs",
        sa.Column("gst_percent", sa.Float(), nullable=False, server_default="18.0"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("invoice_number", sa.String(length=40), nullable=False, unique=True),
        sa.Column("period_start", sa.String(length=10), nullable=False),
        sa.Column("period_end", sa.String(length=10), nullable=False),
        sa.Column("platform_fee", sa.Float(), nullable=False),
        sa.Column("total_jobs", sa.Integer(), nullable=False),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("tier1_rate", sa.Float(), nullable=False),
        sa.Column("tier1_pages", sa.Integer(), nullable=False),
        sa.Column("tier1_amount", sa.Float(), nullable=False),
        sa.Column("tier2_rate", sa.Float(), nullable=False),
        sa.Column("tier2_pages", sa.Integer(), nullable=False),
        sa.Column("tier2_amount", sa.Float(), nullable=False),
        sa.Column("usage_charges", sa.Float(), nullable=False),
        sa.Column("subtotal", sa.Float(), nullable=False),
        sa.Column("gst_percent", sa.Float(), nullable=False),
        sa.Column("gst_amount", sa.Float(), nullable=False),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("by_shipment_type", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="unpaid"),
        sa.Column("paid_at", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "period_start", name="uq_invoices_tenant_period"),
    )


def downgrade() -> None:
    op.drop_table("invoices")
    op.drop_column("tenant_billing_configs", "gst_percent")

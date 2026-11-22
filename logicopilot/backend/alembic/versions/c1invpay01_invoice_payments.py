"""invoice_payments - one Razorpay "Pay now" attempt per row.

Revision ID: c1invpay01
Revises: b2brscripts01
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa

revision = "c1invpay01"
down_revision = "b2brscripts01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("razorpay_order_id", sa.String(40), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(40), nullable=True),
        sa.Column("invoice_amount", sa.Float(), nullable=False),
        sa.Column("fee_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fee_gst_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(10), nullable=False, server_default="created"),
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("paid_at", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_invoice_payments_tenant_id", "invoice_payments", ["tenant_id"])
    op.create_index("ix_invoice_payments_invoice_id", "invoice_payments", ["invoice_id"])
    op.create_index("ix_invoice_payments_razorpay_payment_id", "invoice_payments", ["razorpay_payment_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_payments_razorpay_payment_id", table_name="invoice_payments")
    op.drop_index("ix_invoice_payments_invoice_id", table_name="invoice_payments")
    op.drop_index("ix_invoice_payments_tenant_id", table_name="invoice_payments")
    op.drop_table("invoice_payments")

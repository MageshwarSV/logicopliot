"""Proper GST tax invoice: Axiora's details, customer billing details, numbering and GST split.

* invoice_settings: one row - Axiora's legal name, address, state, GSTIN, email, SAC codes,
  terms, notes, bank details (SaaS Finance -> Invoice settings).
* tenants.billing_*: the customer's legal name, GSTIN, address, state, email, ship-to.
* invoices: invoice_date, due_date, financial_year + fy_sequence (INV/2026-27/0001), seller and
  buyer snapshots, tax_mode and CGST/SGST/IGST amounts. Existing invoices keep their number.

Revision ID: u6taxinvoice01
Revises: u5largezip01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u6taxinvoice01"
down_revision = "u5largezip01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("seller_name", sa.String(255)), sa.Column("address", sa.Text()),
        sa.Column("state_name", sa.String(80)), sa.Column("state_code", sa.String(2)),
        sa.Column("country", sa.String(80)), sa.Column("gstin", sa.String(20)),
        sa.Column("email", sa.String(255)), sa.Column("phone", sa.String(40)),
        sa.Column("sac_platform", sa.String(10)), sa.Column("sac_usage", sa.String(10)),
        sa.Column("terms", sa.String(60)), sa.Column("due_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text()), sa.Column("bank_details", sa.Text()),
        sa.Column("number_prefix", sa.String(10)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for col in (sa.Column("billing_legal_name", sa.String(255)), sa.Column("billing_gstin", sa.String(20)),
                sa.Column("billing_address", sa.Text()), sa.Column("billing_state_name", sa.String(80)),
                sa.Column("billing_state_code", sa.String(2)), sa.Column("billing_email", sa.String(255)),
                sa.Column("billing_ship_to", sa.Text())):
        op.add_column("tenants", col)
    for col in (sa.Column("invoice_date", sa.String(10)), sa.Column("due_date", sa.String(10)),
                sa.Column("financial_year", sa.String(7)), sa.Column("fy_sequence", sa.Integer()),
                sa.Column("seller", sa.JSON()), sa.Column("buyer", sa.JSON()),
                sa.Column("tax_mode", sa.String(10)), sa.Column("cgst_amount", sa.Float()),
                sa.Column("sgst_amount", sa.Float()), sa.Column("igst_amount", sa.Float())):
        op.add_column("invoices", col)
    op.create_index("ix_invoices_financial_year", "invoices", ["financial_year"])


def downgrade() -> None:
    op.drop_index("ix_invoices_financial_year", table_name="invoices")
    for c in ("igst_amount", "sgst_amount", "cgst_amount", "tax_mode", "buyer", "seller",
              "fy_sequence", "financial_year", "due_date", "invoice_date"):
        op.drop_column("invoices", c)
    for c in ("billing_ship_to", "billing_email", "billing_state_code", "billing_state_name",
              "billing_address", "billing_gstin", "billing_legal_name"):
        op.drop_column("tenants", c)
    op.drop_table("invoice_settings")

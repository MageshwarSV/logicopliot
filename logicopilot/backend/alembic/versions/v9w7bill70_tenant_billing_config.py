"""tenant_billing_configs: per-tenant pricing/billing reference numbers a Super Admin records
against third-party services this tenant is billed for (CARGORA's own platform fee + two-tier
AI page-processing rate, and the TrackContainer.in plan this tenant is on). Pure reference
data - one row per tenant, created on first save, never tied to any billing-enforcement logic.

Revision ID: v9w7bill70
Revises: u8v6gk2ops69
"""
from alembic import op
import sqlalchemy as sa

revision = "v9w7bill70"
down_revision = "u8v6gk2ops69"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_billing_configs",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("cargora_monthly_fee", sa.Float(), nullable=False, server_default="4999.0"),
        sa.Column("cargora_tier1_rate_per_page", sa.Float(), nullable=False, server_default="2.50"),
        sa.Column("cargora_tier1_page_limit", sa.Integer(), nullable=False, server_default="25000"),
        sa.Column("cargora_tier2_rate_per_page", sa.Float(), nullable=False, server_default="2.00"),
        sa.Column("trackcontainer_plan", sa.String(length=20), nullable=True),
        sa.Column("trackcontainer_starter_price", sa.Float(), nullable=False, server_default="2999.0"),
        sa.Column("trackcontainer_starter_container_limit", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("trackcontainer_professional_price", sa.Float(), nullable=False, server_default="5999.0"),
        sa.Column(
            "trackcontainer_professional_container_limit", sa.Integer(), nullable=False, server_default="1500"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("tenant_billing_configs")

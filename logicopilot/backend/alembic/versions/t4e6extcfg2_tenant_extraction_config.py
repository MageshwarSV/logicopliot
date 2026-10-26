"""tenant_extraction_configs: per-tenant extraction pause + OpenAI key mode, independent of
the existing global SystemSetting.extraction_paused and the global OpenAI key pool. One row
per tenant, created on first save - no row yet means a tenant behaves exactly as today (not
paused, shared pool). Also adds a nullable tenant_id to openai_api_keys: NULL (every existing
row's value, unchanged) means the global pool; set means that one tenant's own pool/key.

Revision ID: t4e6extcfg2
Revises: s9f3finrole1
"""
from alembic import op
import sqlalchemy as sa

revision = "t4e6extcfg2"
down_revision = "s9f3finrole1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_extraction_configs",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("extraction_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("openai_key_mode", sa.String(length=10), nullable=False, server_default="shared"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.add_column("openai_api_keys", sa.Column("tenant_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_openai_api_keys_tenant_id", "openai_api_keys", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_openai_api_keys_tenant_id", "openai_api_keys", type_="foreignkey")
    op.drop_column("openai_api_keys", "tenant_id")
    op.drop_table("tenant_extraction_configs")

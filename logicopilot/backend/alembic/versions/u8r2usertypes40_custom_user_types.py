"""user_types — Tenant Admin's own custom user types, layered on operator/gk2/manager.

Revision ID: u8r2usertypes40
Revises: t7q1etadate39
"""
from alembic import op
import sqlalchemy as sa

revision = "u8r2usertypes40"
down_revision = "t7q1etadate39"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_types",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_role", sa.String(length=20), nullable=False),
        sa.Column("write_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("base_role IN ('operator', 'gk2', 'manager')", name="ck_user_types_base_role_valid"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_user_types_tenant_name"),
    )
    op.add_column(
        "users",
        sa.Column("user_type_id", sa.String(length=36), sa.ForeignKey("user_types.id", ondelete="RESTRICT"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "user_type_id")
    op.drop_table("user_types")

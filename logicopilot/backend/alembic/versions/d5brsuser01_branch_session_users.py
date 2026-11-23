"""Add erp_branch_session_users - which users may turn which branch login on and off.

Revision ID: d5brsuser01
Revises: d4edocket01
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "d5brsuser01"
down_revision = "d4edocket01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_branch_session_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_session_id", sa.String(36),
                  sa.ForeignKey("erp_branch_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "branch_session_id", name="uq_branch_session_user"),
    )
    op.create_index("ix_erp_branch_session_users_tenant_id", "erp_branch_session_users", ["tenant_id"])
    op.create_index("ix_erp_branch_session_users_user_id", "erp_branch_session_users", ["user_id"])
    op.create_index("ix_erp_branch_session_users_branch_session_id", "erp_branch_session_users",
                    ["branch_session_id"])


def downgrade() -> None:
    op.drop_index("ix_erp_branch_session_users_branch_session_id", table_name="erp_branch_session_users")
    op.drop_index("ix_erp_branch_session_users_user_id", table_name="erp_branch_session_users")
    op.drop_index("ix_erp_branch_session_users_tenant_id", table_name="erp_branch_session_users")
    op.drop_table("erp_branch_session_users")

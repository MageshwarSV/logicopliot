"""Always-on ERP branch sessions, a branch field on ERP scripts, and Auto ERP workers.

- erp_branch_sessions: one always-on ERP login per branch (app/core/branch_sessions.py)
- erp_scripts.branch_field: the job field that routes a job to its branch's session
- system_settings.erp_script_workers_auto: let the server size the ERP worker count

Revision ID: b1brsess01
Revises: u7largejob01
Create Date: 2026-11-20
"""

from alembic import op
import sqlalchemy as sa

revision = "b1brsess01"
down_revision = "u7largejob01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_branch_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch", sa.String(100), nullable=False),
        sa.Column("login_script_id", sa.String(36), sa.ForeignKey("erp_scripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("max_tabs", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="off"),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("live_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_erp_branch_sessions_tenant_id", "erp_branch_sessions", ["tenant_id"])
    op.add_column("erp_scripts", sa.Column("branch_field", sa.String(255), nullable=True))
    op.add_column(
        "system_settings",
        sa.Column("erp_script_workers_auto", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("system_settings", "erp_script_workers_auto")
    op.drop_column("erp_scripts", "branch_field")
    op.drop_index("ix_erp_branch_sessions_tenant_id", table_name="erp_branch_sessions")
    op.drop_table("erp_branch_sessions")

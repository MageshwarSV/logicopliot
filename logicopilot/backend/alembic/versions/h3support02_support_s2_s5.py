"""Production Support S2-S5: extraction start/finish times on jobs, the support activity log,
and support settings (SLA minutes, alert emails).

Revision ID: h3support02
Revises: h2support01
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "h3support02"
down_revision = "h2support01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("extraction_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("system_settings", sa.Column("support_config", sa.JSON(), nullable=True))
    op.create_table(
        "support_activity",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("alert_key", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_activity_kind", "support_activity", ["kind"])
    op.create_index("ix_support_activity_tenant_id", "support_activity", ["tenant_id"])
    op.create_index("ix_support_activity_job_id", "support_activity", ["job_id"])
    op.create_index("ix_support_activity_alert_key", "support_activity", ["alert_key"])
    op.create_index("ix_support_activity_created_at", "support_activity", ["created_at"])


def downgrade() -> None:
    op.drop_table("support_activity")
    op.drop_column("system_settings", "support_config")
    op.drop_column("jobs", "extraction_finished_at")
    op.drop_column("jobs", "extraction_started_at")

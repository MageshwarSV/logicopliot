"""Support mobile app: note status, known-issue fixes, phones for push alerts.

  support_activity.outcome   investigating | fixed | customer_informed | erp_side | not_our_issue
  support_known_issues       the fix written once per kind of failure (key unique)
  support_devices            Expo push tokens of support people's phones

Additive only.

Revision ID: j1supportapp01
Revises: i5thirdw01
Create Date: 2026-11-28
"""

from alembic import op
import sqlalchemy as sa

revision = "j1supportapp01"
down_revision = "i5thirdw01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_activity", sa.Column("outcome", sa.String(20), nullable=True))
    op.create_table(
        "support_known_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("fix", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_known_issues_key", "support_known_issues", ["key"], unique=True)
    op.create_table(
        "support_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("push_token", sa.String(200), nullable=False, unique=True),
        sa.Column("platform", sa.String(10), nullable=False, server_default=""),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_devices_user_id", "support_devices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_support_devices_user_id", table_name="support_devices")
    op.drop_table("support_devices")
    op.drop_index("ix_support_known_issues_key", table_name="support_known_issues")
    op.drop_table("support_known_issues")
    op.drop_column("support_activity", "outcome")

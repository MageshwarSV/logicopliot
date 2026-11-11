"""Add notification_reads - who has already seen which job's ERP outcome.

The notifications themselves are NOT stored: they are derived live from the jobs a user can
already see, using the same two conditions the Jobs list sidebar counts. This table holds only
the read side, so the bell can be dismissed per person without inventing a history.

Revision ID: e2n5notif01
Revises: d1t4datepk1
Create Date: 2026-11-11
"""

from alembic import op
import sqlalchemy as sa

revision = "e2n5notif01"
down_revision = "d1t4datepk1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_reads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_id", sa.String(36),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        # NOT NULL to match TimestampMixin, with a server default so a row inserted by
        # anything other than the ORM still gets one.
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", "kind", name="uq_notification_read"),
    )


def downgrade() -> None:
    op.drop_table("notification_reads")

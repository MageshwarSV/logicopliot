"""Add jobs.validation_approved_at/_by_id and gk2_validation_approved_at/_by_id - who pressed
"Approved and Proceed" on Data Validation (GK1's own pass and GK2's separate re-review pass)
and when, so the Workforce Productivity dashboard can reconstruct real review-time/FTE
figures per employee instead of showing invented numbers. See
app/core/dashboard_metrics.py's compute_workforce_metrics for how these are used.

Additive and nullable - every existing job reads as "no actor/timestamp recorded", which is
correct: these columns did not exist before this, so nothing before it could have recorded
one.

Revision ID: g7w0wrkf1
Revises: f6v9reext1
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

revision = "g7w0wrkf1"
down_revision = "f6v9reext1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("validation_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("validation_approved_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("jobs", sa.Column("gk2_validation_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("gk2_validation_approved_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "gk2_validation_approved_by_id")
    op.drop_column("jobs", "gk2_validation_approved_at")
    op.drop_column("jobs", "validation_approved_by_id")
    op.drop_column("jobs", "validation_approved_at")

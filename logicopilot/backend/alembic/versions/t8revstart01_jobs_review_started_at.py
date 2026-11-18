"""Add jobs.review_started_at - when an operator first acted on an extracted job.

"Review Time" runs from the moment the operator starts working the job (first corrected
value, first page submitted, first Next) to the Final Submit press. Stamped from now on by
jobs._mark_review_started. Jobs from before get the earliest of the two traces that already
exist: their first "Moved on to ..." stage event, or the earliest saved correction.

Revision ID: t8revstart01
Revises: s7erpfail02
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa

revision = "t8revstart01"
down_revision = "s7erpfail02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE jobs SET review_started_at = LEAST("
        "(SELECT MIN(e.created_at) FROM job_events e "
        "  WHERE e.job_id = jobs.id AND e.note LIKE 'Moved on to %'), "
        "(SELECT MIN(v.updated_at) FROM job_field_values v "
        "  WHERE v.job_id = jobs.id AND v.corrected_value IS NOT NULL)) "
        "WHERE status NOT IN ('draft', 'extracting')"
    )


def downgrade() -> None:
    op.drop_column("jobs", "review_started_at")

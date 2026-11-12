"""Add jobs.erp_submitted_at - when this job was LAST submitted to the ERP.

The dashboards' "ERP Entry Time" was completed_at - ai_submitted_at. ai_submitted_at is only
ever set once (the FIRST submission, `if job.ai_submitted_at is None`), while completed_at is
overwritten on every re-completion. A job that failed, was retried and finished weeks later
therefore showed the whole gap as entry time: 502h for MAA/SI/03737/26-27 in production
(first submitted 22 Oct, finally completed 12 Nov), 624h for MAA/SI/03370/26-27.

ai_submitted_at keeps its meaning (the Jobs list's Submitted/Completed split depends on it
being "went through the AI path at least once"). This is a separate column, overwritten on
every submission, so ERP Entry Time = completed_at - erp_submitted_at measures only the last
attempt.

No backfill: job_events never recorded the moment a submission started, so there is nothing
honest to fill it from. Jobs completed before this column existed fall back in
dashboard_metrics._job_stage_durations (see there).

Revision ID: f3erpsub01
Revises: e2n5notif01
Create Date: 2026-11-12
"""

from alembic import op
import sqlalchemy as sa

revision = "f3erpsub01"
down_revision = "e2n5notif01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_submitted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "erp_submitted_at")

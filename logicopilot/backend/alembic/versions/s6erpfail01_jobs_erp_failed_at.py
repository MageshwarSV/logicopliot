"""Add jobs.erp_failed_at - when the ERP entry last failed, shown under the "Failed" badge.

The Jobs list shows when a job went to the ERP under its "ERP Submitted" badge (completed_at);
a failed job showed no time at all. Stamped from now on by a listener on Job.gk2_status (see
app/models/job.py). Jobs failed before this column existed get the time of their latest ERP
failure report (job_failure_reports.created_at - one is written at every failure), falling
back to updated_at only if they have none.

Revision ID: s6erpfail01
Revises: r5impgst01
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa

revision = "s6erpfail01"
down_revision = "r5impgst01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_failed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE jobs SET erp_failed_at = COALESCE("
        "(SELECT MAX(r.created_at) FROM job_failure_reports r WHERE r.job_id = jobs.id), updated_at) "
        "WHERE gk2_status = 'failed'"
    )


def downgrade() -> None:
    op.drop_column("jobs", "erp_failed_at")

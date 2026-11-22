"""Fill jobs.erp_failed_at for jobs whose job.status is "failed".

s6erpfail01 only filled jobs failed through gk2_status. A job can also show "Failed" because
job.status itself is "failed" (the older ERP flow, a run stopped by hand, a crashed run) - e.g.
MAA/SI/03325/26-27 on SIT showed no time. From now on a listener on Job.status stamps these
too; this fills the ones already failed, from their latest failure report or updated_at.

Revision ID: s7erpfail02
Revises: s6erpfail01
Create Date: 2026-11-18
"""

from alembic import op

revision = "s7erpfail02"
down_revision = "s6erpfail01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE jobs SET erp_failed_at = COALESCE("
        "(SELECT MAX(r.created_at) FROM job_failure_reports r WHERE r.job_id = jobs.id), updated_at) "
        "WHERE status = 'failed' AND erp_failed_at IS NULL"
    )


def downgrade() -> None:
    pass

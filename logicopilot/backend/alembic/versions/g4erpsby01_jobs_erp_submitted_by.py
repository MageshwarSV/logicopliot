"""Add jobs.erp_submitted_by_id - WHO last sent this job to the ERP.

The notification bell's "Submitted to ERP" / "Failed to submit to ERP" must reach only the
person who submitted the job. Nothing recorded that: the bell guessed from who created the
job, who it was routed to, and who gave the GK1 / GK2 approval, so one result landed on up to
four people's bells.

Set by the three buttons that send a job to the ERP (see Job.erp_submitted_by_id).

Backfilled as a best guess so the last 30 days of results do not vanish from every bell on
deploy: the person who gave the FINAL approval before the run - the GK1 approver when GK2 was
skipped or never involved, otherwise the GK2 approver. That is who pressed submit on the
normal path; a GK1 retry of a failed GK2 run is the one case it misses, and the next real
submission overwrites the guess anyway.

Revision ID: g4erpsby01
Revises: f3erpsub01
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa

revision = "g4erpsby01"
down_revision = "f3erpsub01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("erp_submitted_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
    )
    op.create_index("ix_jobs_erp_submitted_by_id", "jobs", ["erp_submitted_by_id"])
    op.execute(
        """
        UPDATE jobs
        SET erp_submitted_by_id = CASE
            WHEN gk2_skipped OR gk2_status IS NULL THEN validation_approved_by_id
            ELSE gk2_validation_approved_by_id
        END
        WHERE erp_submitted_by_id IS NULL
          AND (ai_submitted_at IS NOT NULL OR erp_status IS NOT NULL
               OR gk2_status IN ('preparing_erp', 'entering_erp', 'submitted', 'failed'))
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_erp_submitted_by_id", table_name="jobs")
    op.drop_column("jobs", "erp_submitted_by_id")

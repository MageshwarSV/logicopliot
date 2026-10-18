"""Add jobs.ai_submitted_at - a durable "this job went through the AI/ERP automation at
least once" marker, independent of gk2_status (which a later retry through the regular
entry screen resets to None on purpose - see persist_run_outcome's own comment). Without
this, a job that genuinely went through the AI path but was later re-run outside it fell
straight into Completed on the Jobs list with nobody ever having pressed Verified - exactly
the bug found live in production right after this feature shipped.

Backfills every job that shows real evidence of having gone through that path: gk2_skipped
(set once by the GK2-skip approval flow and never cleared again - the one durable survivor
of a later gk2_status reset) or a CURRENT gk2_status of "submitted" (covers a full GK2 review
whose gk2_status has not since been reset). A full-review job whose gk2_status was ALREADY
reset by an earlier retry, before this migration ever ran, has no surviving signal to backfill
from - accepted gap, since nothing in the schema recorded that case; every NEW submission from
here on sets this column correctly and it will never drift again.

Revision ID: d7e5aisub78
Revises: c6d4jbv77
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa

revision = "d7e5aisub78"
down_revision = "c6d4jbv77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("ai_submitted_at", sa.DateTime(timezone=True), nullable=True))

    conn = op.get_bind()
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String),
        sa.column("gk2_skipped", sa.Boolean),
        sa.column("gk2_status", sa.String),
        sa.column("erp_verified_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("ai_submitted_at", sa.DateTime(timezone=True)),
    )
    rows = conn.execute(
        sa.select(jobs.c.id, jobs.c.erp_verified_at, jobs.c.updated_at).where(
            (jobs.c.gk2_skipped.is_(True)) | (jobs.c.gk2_status == "submitted")
        )
    ).fetchall()
    for job_id, erp_verified_at, updated_at in rows:
        conn.execute(
            jobs.update().where(jobs.c.id == job_id)
            .values(ai_submitted_at=erp_verified_at or updated_at)
        )
    print(f"d7e5aisub78: backfilled ai_submitted_at on {len(rows)} job(s)")


def downgrade() -> None:
    op.drop_column("jobs", "ai_submitted_at")

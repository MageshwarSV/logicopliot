"""Add jobs.completed_at - the date a job actually FINISHED, as opposed to the date it was
created, and backfill it from the job_events history.

The Jobs list showed each job's created_at under the "Date" column, and the Manager mobile
app needs to count jobs by the day they completed. Those are routinely different days: of the
first four completed jobs in production, TWO finished on a later day than they were created,
so half of them were being reported on the wrong day.

No existing column could answer it. ai_submitted_at is only set on the AI/GK2 path (three of
those same four had it NULL), and updated_at moves on any edit at all - three jobs share one
updated_at value to the microsecond, left by a bulk update. The only honest record was the
job_events history, so this backfills from there and the before_flush listener in
app/db/job_history.py keeps it current from then on.

MAX, not MIN: three of those four jobs completed more than once, having bounced back for a
retry. A job belongs to the day it FINALLY finished - taking the first would date it to a
completion that was later undone, and would let one job be counted on two different days.

Revision ID: t0g4jobcomp2
Revises: t4e6extcfg2
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa

revision = "t0g4jobcomp2"
# Re-parented onto t4e6extcfg2 rather than the s9f3finrole1 this was written against: the
# tenant-extraction-config migration landed on main from another branch in the meantime and
# claimed that same parent. Two migrations with one parent are two alembic heads, and
# `alembic upgrade head` refuses to run at all with more than one - the deploy would have
# stopped here. Order between the two does not matter; they touch different tables.
down_revision = "t4e6extcfg2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_completed_at", "jobs", ["completed_at"])

    # Spelled as a correlated subquery rather than UPDATE..FROM/JOIN so the same statement
    # runs on PostgreSQL (deploy) and SQLite (the test suite), which disagree about that
    # syntax. The EXISTS guard keeps rows with no completed event untouched at NULL instead of
    # writing NULL over them.
    op.execute(
        """
        UPDATE jobs SET completed_at = (
            SELECT MAX(e.created_at) FROM job_events e
            WHERE e.job_id = jobs.id AND e.status = 'completed'
        )
        WHERE EXISTS (
            SELECT 1 FROM job_events e
            WHERE e.job_id = jobs.id AND e.status = 'completed'
        )
        """
    )

    # Fallback for any job whose history does not reach back far enough to hold its completion
    # (the events table is younger than the jobs table). ai_submitted_at is the right second
    # choice and not a guess: on the one job that carries both, it matches that job's last
    # completed event to the same second.
    op.execute(
        """
        UPDATE jobs SET completed_at = ai_submitted_at
        WHERE completed_at IS NULL AND ai_submitted_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_completed_at", table_name="jobs")
    op.drop_column("jobs", "completed_at")

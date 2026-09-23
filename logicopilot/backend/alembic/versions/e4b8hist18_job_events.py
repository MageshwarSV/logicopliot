"""job_events: a job's status history, so an operator can see when each step happened

Revision ID: e4b8hist18
Revises: d3a7lookup17
"""
from alembic import op
import sqlalchemy as sa

revision = "e4b8hist18"
down_revision = "d3a7lookup17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_id", sa.String(36),
                  sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Every job that already exists gets one line, taken from when it was created and where it
    # stands now. Without this the popup opens empty on every job filed before today, which
    # reads as "nothing ever happened" rather than "we only started recording this morning".
    op.execute(
        """
        INSERT INTO job_events (id, tenant_id, job_id, status, stage, note,
                                created_at, updated_at)
        SELECT
            (CASE
                WHEN length(j.id) >= 36 THEN substr(j.id, 1, 32) || 'hist'
                ELSE j.id || '-hist'
             END),
            j.tenant_id, j.id, j.status, NULL,
            'Recorded from the job itself - history began later',
            j.created_at, j.created_at
        FROM jobs j
        """
    )


def downgrade() -> None:
    op.drop_table("job_events")

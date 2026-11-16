"""Add job_deletions - who deleted a job, and when.

A deleted job leaves only its billing rows behind, and the Billing page counts them as "Deleted
Jobs" with no way to say who removed any of them. One row is written per job at the moment it
is deleted (see app.models.job_deletion.JobDeletion). Nothing is back-filled: for a job deleted
before this existed, who did it was never written down anywhere.

Revision ID: o2jobdel01
Revises: n1mrkopt01
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "o2jobdel01"
down_revision = "n1mrkopt01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_deletions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("job_reference", sa.String(length=255), nullable=False),
        sa.Column("deleted_by_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("deleted_by_name", sa.String(length=255), nullable=True),
        sa.Column("deleted_by_email", sa.String(length=320), nullable=True),
        sa.Column("deleted_by_role", sa.String(length=50), nullable=True),
        sa.Column("how", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_job_deletions_tenant_id", "job_deletions", ["tenant_id"])
    op.create_index("ix_job_deletions_job_id", "job_deletions", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_deletions_job_id", table_name="job_deletions")
    op.drop_index("ix_job_deletions_tenant_id", table_name="job_deletions")
    op.drop_table("job_deletions")

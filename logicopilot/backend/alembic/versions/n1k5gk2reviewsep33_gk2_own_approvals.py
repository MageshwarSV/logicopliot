"""gk2 own approvals — job_documents.gk2_approved, jobs.gk2_validation_approved

GK1 and GK2 are two separate sign-offs on the same job. Reusing JobDocument.approved and
Job.validation_approved for both meant GK2 opening a job GK1 already approved saw
everything pre-checked - these two new columns give GK2 their own independent state, so
they review and approve it themselves rather than inheriting GK1's ticks.

Revision ID: n1k5gk2reviewsep33
Revises: m0j4gk2role32
"""
from alembic import op
import sqlalchemy as sa

revision = "n1k5gk2reviewsep33"
down_revision = "m0j4gk2role32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_documents",
        sa.Column("gk2_approved", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "jobs",
        sa.Column("gk2_validation_approved", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "gk2_validation_approved")
    op.drop_column("job_documents", "gk2_approved")

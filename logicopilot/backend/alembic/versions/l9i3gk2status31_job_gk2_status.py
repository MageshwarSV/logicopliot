"""job gk2 status — jobs.gk2_status

The GK1 (operator) -> GK2 (Gate Keeper 2) sign-off chain, layered on top of an already
"extracted" job rather than adding more job.status values. None | pending |
preparing_erp | submitted.

Revision ID: l9i3gk2status31
Revises: k8h2cycle30
"""
from alembic import op
import sqlalchemy as sa

revision = "l9i3gk2status31"
down_revision = "k8h2cycle30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("gk2_status", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "gk2_status")

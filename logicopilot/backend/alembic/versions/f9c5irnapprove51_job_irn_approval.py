"""irn_approval_requested on jobs: which of GK1's two IRN Documents Upload actions was
actually pressed - Approval for IRN (True) vs Skip (False, and every job finished before
this column existed). Read by gk2_approve to decide whether Final Approve & Proceed runs the
real ERP submission or parks the job in a new "IRN Document Process" wait-state instead.

Revision ID: f9c5irnapprove51
Revises: e8b4example50
"""
from alembic import op
import sqlalchemy as sa

revision = "f9c5irnapprove51"
down_revision = "e8b4example50"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("irn_approval_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("jobs", "irn_approval_requested")

"""mail routing: per-operator pull email + job operator assignment

Revision ID: a1b2mailroute01
Revises: 929d115ce880
Create Date: 2026-07-18

Adds:
- template_groups.pull_operator_id  — operator who owns this customer's mail-pulled jobs
- jobs.assigned_operator_id         — if set, only this operator (plus admins) sees the job
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2mailroute01"
down_revision: Union[str, Sequence[str], None] = "929d115ce880"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("pull_operator_id", sa.String(length=36), nullable=True))
    op.add_column("jobs", sa.Column("assigned_operator_id", sa.String(length=36), nullable=True))
    op.create_index("ix_jobs_assigned_operator_id", "jobs", ["assigned_operator_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_assigned_operator_id", table_name="jobs")
    op.drop_column("jobs", "assigned_operator_id")
    op.drop_column("template_groups", "pull_operator_id")

"""jobs.stage_override — manual per-job pipeline stage

Revision ID: a1b3jobstage02
Revises: a1b2mailroute01
Create Date: 2026-07-18
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b3jobstage02"
down_revision: Union[str, Sequence[str], None] = "a1b2mailroute01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("stage_override", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "stage_override")

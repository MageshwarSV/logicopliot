"""template custom ruling prompt

Revision ID: a1b5ruling04
Revises: a1b4customfields03
Create Date: 2026-07-19
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b5ruling04"
down_revision: Union[str, Sequence[str], None] = "a1b4customfields03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("ruling_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("template_groups", "ruling_prompt")

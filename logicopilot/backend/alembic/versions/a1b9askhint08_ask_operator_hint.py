"""ask_operator_hint — the guidance shown to the operator at Submit Entry

A tick on its own gives the operator a blank box. This carries the Super Admin's
explanation, e.g. "T for Transaction, D for Deferred", so they know what to enter.

Revision ID: a1b9askhint08
Revises: a1b8askoper07
Create Date: 2026-08-08
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b9askhint08"
down_revision: Union[str, Sequence[str], None] = "a1b8askoper07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("field_marks", sa.Column("ask_operator_hint", sa.Text(), nullable=True))
    op.add_column("custom_fields", sa.Column("ask_operator_hint", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "ask_operator_hint")
    op.drop_column("field_marks", "ask_operator_hint")

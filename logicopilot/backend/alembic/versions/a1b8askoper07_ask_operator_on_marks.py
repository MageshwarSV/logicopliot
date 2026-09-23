"""field_marks.ask_operator — make the operator confirm a value before ERP entry

Ticked by the Super Admin while drawing the field. Any number of fields on a template may
carry it; Submit Entry is refused until every one has been answered on that job.

Revision ID: a1b8askoper07
Revises: a1b7extracted06
Create Date: 2026-08-08
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b8askoper07"
down_revision: Union[str, Sequence[str], None] = "a1b7extracted06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default so existing rows get False without a table rewrite.
    op.add_column(
        "field_marks",
        sa.Column("ask_operator", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Custom fields need it too: some values (the insurance percentage) appear on no
    # document, so they can only ever come from the operator.
    op.add_column(
        "custom_fields",
        sa.Column("ask_operator", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "ask_operator")
    op.drop_column("field_marks", "ask_operator")

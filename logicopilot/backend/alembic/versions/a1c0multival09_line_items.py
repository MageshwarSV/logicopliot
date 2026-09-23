"""multi-value fields — read a whole line-item table instead of one value

field_marks.is_multi_value   ticked while drawing: this document repeats this field
job_field_values.row_index   which physical table row a value came from (1-based)

Values sharing a row_index come from the same row, which is what allows an ICEGATE ITEMS
sheet to be assembled with description/qty/price correctly aligned.

Revision ID: a1c0multival09
Revises: a1b9askhint08
Create Date: 2026-08-08
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c0multival09"
down_revision: Union[str, Sequence[str], None] = "a1b9askhint08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "field_marks",
        sa.Column("is_multi_value", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # NULL on every existing value = "ordinary single-value field", so nothing changes for
    # jobs already extracted.
    op.add_column("job_field_values", sa.Column("row_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_field_values", "row_index")
    op.drop_column("field_marks", "is_multi_value")

"""custom_fields.per_row — ask the operator once per line item, not once per job

A product entry screen takes one invoice line at a time: description, HS code, quantity,
unit, price, amount, then Update. Most of those come off the invoice, but the CTH / HS code
does not appear on any document — the operator supplies it. And it can differ per product,
so one value for the whole job is not enough: a 14-line invoice needs 14 CTH numbers, each
belonging to a specific line.

`per_row` marks a custom field as one-value-per-line-item. At extraction the job gets one
empty value per line (row_index 1..N, N taken from the line-item rows actually found), the
operator fills them in numbered order, and the synchronised row loop feeds row N's value to
row N of the ERP grid.

Defaults to False, so every existing custom tag keeps asking for exactly one value.

Revision ID: a1c5perrow14
Revises: a1c4askreq13
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c5perrow14"
down_revision = "a1c4askreq13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("per_row", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "per_row")

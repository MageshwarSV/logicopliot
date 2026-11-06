"""Add field_marks.validation_pattern/validation_uppercase/validation_message and the same
three columns on custom_fields - an admin-settable format check (e.g. "exactly 3 letters",
"5 letters then 1 digit") and an auto-uppercase-on-save toggle, through the Template Creation
Wizard instead of a hand-coded check in JobRunPage.tsx per field (Terms of Invoice,
Customs House Code and Shipment Country Code each got one by hand this session - this
generalizes that into something any field can have, with no deploy needed).

Purely client-side, same as those three hand-coded checks already are - nothing is enforced
server-side by this migration or the columns it adds.

Additive and nullable (validation_uppercase defaults False) - every existing field keeps
validating exactly as it does today (the three hand-coded checks keep working unchanged; every
other field keeps accepting anything, unchanged).

Revision ID: a7p1validt1
Revises: z6m9ddstyl1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "a7p1validt1"
down_revision = "z6m9ddstyl1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(table, sa.Column("validation_pattern", sa.Text(), nullable=True))
        op.add_column(
            table,
            sa.Column("validation_uppercase", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.add_column(table, sa.Column("validation_message", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "validation_message")
        op.drop_column(table, "validation_uppercase")
        op.drop_column(table, "validation_pattern")

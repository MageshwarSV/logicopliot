"""Add field_marks.date_picker/date_format and the same two columns on custom_fields - an
admin-settable "this field holds a date" toggle, set once in the Template Creation Wizard,
that puts a calendar button beside the box on the Job Run screen.

The toggle is PURELY additive at the UI level: the text box stays exactly as it is, still
showing (and still accepting) whatever the document actually said. The calendar is a second
way to set the same value, for an operator who would rather click a day than type one.

date_format is the fallback used only when the box is EMPTY and there is therefore no existing
value whose format a pick could be written back in. Blank (the default, and every field that
predates this) means day-first DD/MM/YYYY - the house convention this product already reads
ambiguous dates with, see extraction._DATE_FORMATS' own comment.

Additive and nullable/default-false - every existing field renders exactly as it does today.

Revision ID: d1t4datepk1
Revises: h8x1notrq1
Create Date: 2026-11-10
"""

from alembic import op
import sqlalchemy as sa

revision = "d1t4datepk1"
down_revision = "h8x1notrq1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column("date_picker", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.add_column(table, sa.Column("date_format", sa.String(20), nullable=True))


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "date_format")
        op.drop_column(table, "date_picker")

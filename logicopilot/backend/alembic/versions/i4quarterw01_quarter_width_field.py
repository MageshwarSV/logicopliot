"""Add field_marks.quarter_width and custom_fields.quarter_width - a third width choice beside
half (default) and full, so short fields (a currency code beside its amount) can share a row
three or four at a time on the job screen.

Additive, default false - every existing field keeps its current width.

Revision ID: i4quarterw01
Revises: i3manualinv01
Create Date: 2026-11-28
"""

from alembic import op
import sqlalchemy as sa

revision = "i4quarterw01"
down_revision = "i3manualinv01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column("quarter_width", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "quarter_width")

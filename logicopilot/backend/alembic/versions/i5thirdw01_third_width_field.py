"""Add field_marks.third_width and custom_fields.third_width - three equal fields filling one
row of the job screen (Quarter fits four, Half two, Full one).

Additive, default false - every existing field keeps its current width.

Revision ID: i5thirdw01
Revises: i4quarterw01
Create Date: 2026-11-28
"""

from alembic import op
import sqlalchemy as sa

revision = "i5thirdw01"
down_revision = "i4quarterw01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column("third_width", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "third_width")

"""Add composite_separators (custom_fields) and separators (composite_field_consignee_defaults)
- Combined Description Configuration: each piece of a composite field now has its own
configurable "separator before" (None/space/comma/comma+space/hyphen), instead of every pair of
pieces always joining with a fixed single space. Both columns are additive and nullable - a
field or consignee default saved before this existed simply has no separators row yet, and
falls back to the single-space join it already had (see _join_composite_pieces in jobs.py).

Revision ID: f3a9sep80
Revises: e8f6docval79
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa

revision = "f3a9sep80"
down_revision = "e8f6docval79"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("composite_separators", sa.JSON(), nullable=True))
    op.add_column("composite_field_consignee_defaults", sa.Column("separators", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("composite_field_consignee_defaults", "separators")
    op.drop_column("custom_fields", "composite_separators")

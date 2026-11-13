"""Add custom_fields.requires_value_of - "fill only when <another field> has a value".

MAWB Date must be empty whenever MAWB Number is: a plain House Air Waybill has no master
waybill, so it has no master date either, and a date read off it anyway is the house date in
the wrong box. The two are read from the document separately, so nothing tied them together.
See CustomField.requires_value_of. Nullable, no backfill - no field has the condition until a
Super Admin sets one.

Revision ID: i6reqval01
Revises: h5ctrbox01
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa

revision = "i6reqval01"
down_revision = "h5ctrbox01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("requires_value_of", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "requires_value_of")

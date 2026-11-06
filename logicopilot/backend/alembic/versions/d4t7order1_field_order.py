"""Add field_marks.field_order and the same column on custom_fields - an admin-settable
"where this field sits among its siblings under the same section heading" number, through the
Template Creation Wizard, so reordering a field never needs a code deploy again. None (every
field that predates this column) falls back to whatever position the old hardcoded
FIELD_GROUPS_BY_DOC_TYPE label-order list in JobRunPage.tsx already gives it, unchanged.

Additive and nullable - no default/backfill, since "unset" is itself the meaningful value that
preserves today's existing order for every field.

Revision ID: d4t7order1
Revises: c9s3mline1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "d4t7order1"
down_revision = "c9s3mline1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(table, sa.Column("field_order", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "field_order")

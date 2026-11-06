"""Add field_marks.display_label and custom_fields.display_label - what the OPERATOR sees on
the Job Run page, when it should read differently from label_name (which stays what
extraction/matching/the Excel export all key on, unchanged). label_name was always this
field's own technical identity; display_label is purely cosmetic, same idea as the recently
added standalone_group_heading column, and is checked ahead of JobRunPage.tsx's own hardcoded
FIELD_LABEL_OVERRIDES_BY_DOC_TYPE/BY_GROUP maps rather than replacing them.

Additive and nullable - every existing field keeps showing exactly what it shows today (its
own label_name, or whatever the hardcoded map already overrides it to) with no backfill
needed by this migration itself.

Revision ID: y5k8displ1
Revises: x4j7grphd1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "y5k8displ1"
down_revision = "x4j7grphd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("field_marks", sa.Column("display_label", sa.Text(), nullable=True))
    op.add_column("custom_fields", sa.Column("display_label", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "display_label")
    op.drop_column("field_marks", "display_label")

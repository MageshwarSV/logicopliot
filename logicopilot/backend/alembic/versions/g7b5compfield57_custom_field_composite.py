"""custom_fields.composite_source_labels - a new CustomField.kind="composite": the value for
each product line is assembled purely by reading OTHER already-computed fields on the SAME
line (marks or other custom fields, named by label_name) in a chosen order and joining them
with a single space, skipping any piece that is blank for that line. No AI prompt, no
reference sheet - a pure join over data this job already has.

Revision ID: g7b5compfield57
Revises: f6a4pairfield56
"""
from alembic import op
import sqlalchemy as sa

revision = "g7b5compfield57"
down_revision = "f6a4pairfield56"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("composite_source_labels", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "composite_source_labels")

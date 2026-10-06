"""field_marks.standalone_group_heading - the job screen's heading for a standalone_multi_value
field's own group of rows (e.g. "Container"), wordable per tenant/template the same way
CustomField.picker_heading already lets a picker pair be worded per template.

Revision ID: j0e8standalonehdg60
Revises: i9d7standalonerow59
"""
from alembic import op
import sqlalchemy as sa

revision = "j0e8standalonehdg60"
down_revision = "i9d7standalonerow59"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("field_marks", sa.Column("standalone_group_heading", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("field_marks", "standalone_group_heading")

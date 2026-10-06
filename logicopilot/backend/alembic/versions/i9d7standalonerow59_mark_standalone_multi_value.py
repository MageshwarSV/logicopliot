"""field_marks.standalone_multi_value - marks an is_multi_value field whose rows are their
own table, never meant to align against any other per-row field (a container count on the
bill of lading has nothing to do with how many products are on the invoice).

Revision ID: i9d7standalonerow59
Revises: h8c6consigndef58
"""
from alembic import op
import sqlalchemy as sa

revision = "i9d7standalonerow59"
down_revision = "h8c6consigndef58"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "field_marks",
        sa.Column("standalone_multi_value", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("field_marks", "standalone_multi_value")

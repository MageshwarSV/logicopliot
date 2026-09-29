"""example_value on custom_fields: mirrors FieldMark.example_value - typed live into the ERP
while a Super Admin is RECORDING a script, never the field's real answer on any actual job.
Needed for a "Manual Entry" field created straight from the ERP Script Recorder (an
ask_operator field with no hardcoded_value of its own), which otherwise has nothing to type
during recording but its own label - not a valid value on the live ERP, so the field never
validated and the steps after it could not be recorded.

Revision ID: e8b4example50
Revises: d7a3fuzzy49
"""
from alembic import op
import sqlalchemy as sa

revision = "e8b4example50"
down_revision = "d7a3fuzzy49"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("example_value", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "example_value")

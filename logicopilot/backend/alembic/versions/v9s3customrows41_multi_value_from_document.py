"""custom_fields.multi_value_from_document — AI-computed counterpart to a Mark's
"multiple values in this document" checkbox.

Revision ID: v9s3customrows41
Revises: u8r2usertypes40
"""
from alembic import op
import sqlalchemy as sa

revision = "v9s3customrows41"
down_revision = "u8r2usertypes40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("multi_value_from_document", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "multi_value_from_document")

"""template_groups: entry_mode + excel_config

How a job's data reaches the ERP. Some ERPs take a bulk Excel import instead of dozens of
typed fields, and which one a customer uses is a property of their template set.

Revision ID: b1e7excel15
Revises: a1c5perrow14
"""
from alembic import op
import sqlalchemy as sa

revision = "b1e7excel15"
down_revision = "a1c5perrow14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default so existing rows become "fields" - which is what they have always done -
    # without a second UPDATE pass.
    op.add_column(
        "template_groups",
        sa.Column("entry_mode", sa.String(length=10), nullable=False, server_default="fields"),
    )
    op.add_column("template_groups", sa.Column("excel_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("template_groups", "excel_config")
    op.drop_column("template_groups", "entry_mode")

"""hidden_from_capture on custom_fields: a computed/looked-up field whose only real reader is
something else entirely (an ERP script's own field mapping, e.g. ContainerTypeCode feeding
SOFTLINK's container-type dropdown) never needs an operator's eyes on it - it still computes
and writes a real JobFieldValue on every row exactly as before, it just stops appearing on the
product card or Dump Data. Opt-in per field, same shape as skip_material_master.

Revision ID: s6t4hidecap67
Revises: q5r3mailbox66
"""
from alembic import op
import sqlalchemy as sa

revision = "s6t4hidecap67"
down_revision = "q5r3mailbox66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("hidden_from_capture", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "hidden_from_capture")

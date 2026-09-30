"""custom_fields.paired_custom_field_id / picker_heading / sync_field_ids - lets any two
already-existing custom fields be linked into a picker pair (e.g. "Dump CTH Number" vs
"Document CTH"): the operator sees both fields' per-row values with a single-select
checkbox, and picking one writes it as the PAIRED field's own corrected value (the field
name downstream export actually reads), optionally also syncing onto other named fields
(sync_field_ids) that usually carry the same answer (e.g. RITC following the CTH pick).

Deliberately NOT a new CustomField.kind - each half of a pair stays whatever kind it always
was (hardcoded/ai/lookup), computed exactly as before; pairing only adds picker UI + a
write-through on top of two fields that are each already fully self-sufficient.

Revision ID: f6a4pairfield56
Revises: e5f3mailpause55
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a4pairfield56"
down_revision = "e5f3mailpause55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("paired_custom_field_id", sa.String(36),
                 sa.ForeignKey("custom_fields.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("custom_fields", sa.Column("picker_heading", sa.Text(), nullable=True))
    op.add_column("custom_fields", sa.Column("sync_field_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "sync_field_ids")
    op.drop_column("custom_fields", "picker_heading")
    op.drop_column("custom_fields", "paired_custom_field_id")

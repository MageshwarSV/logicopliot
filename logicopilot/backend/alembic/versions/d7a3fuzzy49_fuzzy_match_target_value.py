"""fuzzy_match on is_target_value fields: opt-in per field, so a target-value lookup can match
on the SAME real-world thing despite OCR/formatting noise ("KUEHNE + NAGEL PVT. LTD." vs
"KUEHNE+NAGEL") instead of requiring byte-identical text - a near-miss is acceptable for a
freight forwarder's name, but this is never turned on for a material code or CTH lookup, which
is why it lives as a flag on the field rather than a blanket behavior change.

Revision ID: d7a3fuzzy49
Revises: c6z2targetval48
"""
from alembic import op
import sqlalchemy as sa

revision = "d7a3fuzzy49"
down_revision = "c6z2targetval48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "field_marks",
        sa.Column("fuzzy_match", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "custom_fields",
        sa.Column("fuzzy_match", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "fuzzy_match")
    op.drop_column("field_marks", "fuzzy_match")

"""lookup_ai_fallback on custom_fields: when a kind="lookup" field's reference sheet has
nothing for a line EITHER, ask an AI classifier to translate the raw value into one of a
small, fixed set of known-correct answers instead of asking an operator or leaving it blank -
built for a value whose real-world spelling varies endlessly (a container type) but whose
actual meaning is always one of a short, closed list a hand-maintained sheet can never fully
anticipate. Off by default, same shape as skip_material_master above it.

Revision ID: n4p2aifallback65
Revises: k3m1skipmaster64
"""
from alembic import op
import sqlalchemy as sa

revision = "n4p2aifallback65"
down_revision = "k3m1skipmaster64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("lookup_ai_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_fields", "lookup_ai_fallback")

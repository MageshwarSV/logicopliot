"""openai_api_keys - a pool of OpenAI API keys a Super Admin can add, round-robinned across

Purely additive: a new table, no existing column touched. With no rows (every server's
state until a Super Admin adds one), every OpenAI call site behaves exactly as it does
today - app/core/openai_pool.py falls back to the single Settings key. See that module's
own docstring, and app/models/openai_api_key.py's, for the full design.

Revision ID: m2k0keypool63
Revises: l0j6engine62
"""
from alembic import op
import sqlalchemy as sa

revision = "m2k0keypool63"
down_revision = "l0j6engine62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "openai_api_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("openai_api_keys")

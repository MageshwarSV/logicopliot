"""system_settings.openai_balance_usd / openai_balance_expiry — manually-entered OpenAI
account balance + expiry for the Spend Analytics page (no OpenAI API, including the Admin
key, exposes this; typed in by a Super Admin instead)

Revision ID: y2v6openaibalance44
Revises: x1u5filterpg43
"""
from alembic import op
import sqlalchemy as sa

revision = "y2v6openaibalance44"
down_revision = "x1u5filterpg43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("openai_balance_usd", sa.Float(), nullable=True))
    op.add_column("system_settings", sa.Column("openai_balance_expiry", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_settings", "openai_balance_expiry")
    op.drop_column("system_settings", "openai_balance_usd")

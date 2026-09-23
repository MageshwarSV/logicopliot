"""system_settings.openai_admin_key_encrypted — a separate OpenAI Admin API key, used only
to read real organization cost/usage data for Spend Analytics.

Revision ID: w0t4openaiadmin42
Revises: v9s3customrows41
"""
from alembic import op
import sqlalchemy as sa

revision = "w0t4openaiadmin42"
down_revision = "v9s3customrows41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("openai_admin_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_settings", "openai_admin_key_encrypted")

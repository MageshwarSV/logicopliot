"""system settings openai key — system_settings.openai_api_key_encrypted

Lets a Super Admin set/rotate the OpenAI API key from Settings instead of editing .env and
restarting - encrypted at rest, never returned once saved. NULL means "use the .env default".

Revision ID: i6f0aikey28
Revises: h5e9sysset27
"""
from alembic import op
import sqlalchemy as sa

revision = "i6f0aikey28"
down_revision = "h5e9sysset27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("openai_api_key_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_settings", "openai_api_key_encrypted")

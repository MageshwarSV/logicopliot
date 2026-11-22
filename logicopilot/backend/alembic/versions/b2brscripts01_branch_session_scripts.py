"""erp_branch_sessions.script_ids - which stream scripts may use a branch login (NULL = all).

Revision ID: b2brscripts01
Revises: b1brsess01
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa

revision = "b2brscripts01"
down_revision = "b1brsess01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("erp_branch_sessions", sa.Column("script_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("erp_branch_sessions", "script_ids")

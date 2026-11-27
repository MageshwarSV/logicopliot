"""ERP column rules per template (template_groups.erp_column_rules).

Revision ID: h4erprules01
Revises: h3support02
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "h4erprules01"
down_revision = "h3support02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("erp_column_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("template_groups", "erp_column_rules")

"""ERP import rejection explained per Cargora box (jobs.erp_help).

Revision ID: h1erphelp01
Revises: g3tariffjobs01
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "h1erphelp01"
down_revision = "g3tariffjobs01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("erp_help", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "erp_help")

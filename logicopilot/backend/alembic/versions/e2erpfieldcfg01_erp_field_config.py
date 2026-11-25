"""Onboarding Step 4: erp_fields.config - each field's category-specific configuration
(fixed value + per-importer values, operator input type and choices, ...).

Revision ID: e2erpfieldcfg01
Revises: e1erpdocmap01
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa

revision = "e2erpfieldcfg01"
down_revision = "e1erpdocmap01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("erp_fields", sa.Column("config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("erp_fields", "config")

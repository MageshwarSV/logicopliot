"""Add custom_field_reference_values.is_active - switch a master-data row off without deleting it.

Tenant Admin's ERP Master Data - Importer screen no longer deletes an importer branch; it makes
it Inactive. An inactive row stays in the table (and still blocks the same key being added
twice) but is never offered in a job's dropdowns or matched by a lookup. Every existing row is
active.

Revision ID: q4refact01
Revises: p3refgst01
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "q4refact01"
down_revision = "p3refgst01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_field_reference_values",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("custom_field_reference_values", "is_active")

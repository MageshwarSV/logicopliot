"""Add custom_field_reference_values.gstin - the importer's GSTIN, kept on its branch-address row.

Tenant Admin's ERP Master Data - Importer screen lists each importer branch (ERP Branch Address
reference rows: name + branch + country -> address) with its GSTIN. The GSTIN is not part of
the lookup key (the job screen still matches name + branch + country exactly as before), so it
gets its own nullable column rather than a match_value slot. Null on every other reference row.

Revision ID: p3refgst01
Revises: o2jobdel01
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "p3refgst01"
down_revision = "o2jobdel01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_field_reference_values", sa.Column("gstin", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_field_reference_values", "gstin")

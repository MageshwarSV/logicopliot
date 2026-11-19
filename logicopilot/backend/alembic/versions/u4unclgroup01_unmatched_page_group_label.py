"""unmatched_upload_pages.group_label - a heading for Unclassified pages.

Used for the house waybills of a consolidation (an air consol zip: master waybill in the main
folder, one sub-folder per house shipment): only the main folder's waybill fills the Air
Waybill slot; the sub-folder ones stay in Unclassified, grouped under "Air Waybill".

Revision ID: u4unclgroup01
Revises: u3zippwd01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u4unclgroup01"
down_revision = "u3zippwd01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("unmatched_upload_pages", sa.Column("group_label", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("unmatched_upload_pages", "group_label")

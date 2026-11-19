"""template_groups.large_zip_hold / large_zip_mb - hold extraction for a large zip.

A mail (or upload) carrying a zip larger than large_zip_mb (default 5 MB - a big consolidation)
is read, classified and placed, then waits for Run extraction so its documents can be checked
first; every other job extracts automatically. Off for every existing template - switched on
per template on Super Admin -> Document Processing.

Revision ID: u5largezip01
Revises: u4unclgroup01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u5largezip01"
down_revision = "u4unclgroup01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("large_zip_hold", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("template_groups", sa.Column("large_zip_mb", sa.Integer(), nullable=False, server_default="5"))


def downgrade() -> None:
    op.drop_column("template_groups", "large_zip_mb")
    op.drop_column("template_groups", "large_zip_hold")

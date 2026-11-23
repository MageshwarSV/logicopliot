"""Add jobs.edocket_state - the E-Docket page's saved choices (printed from Print pages too).

Revision ID: d4edocket01
Revises: d3apicost01
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "d4edocket01"
down_revision = "d3apicost01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("edocket_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "edocket_state")

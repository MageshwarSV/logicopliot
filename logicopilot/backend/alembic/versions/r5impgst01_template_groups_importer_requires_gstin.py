"""Add template_groups.importer_requires_gstin - offer an Indian importer only once it has a GSTIN.

With it on, the importer name and branch lists on a job leave out every Indian importer branch
whose GSTIN is not in the dump yet (see app/core/importer_gstin.py). Off for every template:
no GSTIN has been entered anywhere at the time this is added, so on by default would empty the
Indian importer list on every job. It is switched on per template once the numbers are in.

Revision ID: r5impgst01
Revises: q4refact01
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "r5impgst01"
down_revision = "q4refact01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_groups",
        sa.Column("importer_requires_gstin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("template_groups", "importer_requires_gstin")

"""Add jobs.extraction_is_rerun - lets the badge say "AI Re-Extraction" (In Review -> AI
Re-Extraction -> In Review) while a Re-run extraction / Extract this one run is in flight,
instead of the same "AI Processing" text a job's first-ever extraction shows. Set in
_begin_extraction from the existing is_rerun flag every extraction trigger already computes
for billing; read only by _outer_status's own status=="extracting" branch.

Additive and nullable-default-false - every existing job reads as "not currently a rerun",
which is correct since none of them are mid-extraction right now.

Revision ID: f6v9reext1
Revises: e5u8rerun1
Create Date: 2026-11-09
"""

from alembic import op
import sqlalchemy as sa

revision = "f6v9reext1"
down_revision = "e5u8rerun1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("extraction_is_rerun", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("jobs", "extraction_is_rerun")

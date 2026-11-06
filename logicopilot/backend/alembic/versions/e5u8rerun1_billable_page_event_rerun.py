"""Add billable_page_events.is_rerun - distinguishes an explicit admin re-run of an already-
processed job's classification (reclassify_documents) from the job's own normal first-time
processing (Smart Upload, email, manual upload - including a large file's own multi-pass OCR
past the per-file cap, which is still one original pass, not a rerun).

Additive and nullable-default-false - every existing ledger row (all of tonight's and all
historical billing) is treated as "original", which is correct: is_rerun did not exist before
this, so nothing before it could have been an explicit reclassify-triggered rerun event as
opposed to the job's own first processing.

Revision ID: e5u8rerun1
Revises: d4t7order1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "e5u8rerun1"
down_revision = "d4t7order1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "billable_page_events",
        sa.Column("is_rerun", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("billable_page_events", "is_rerun")

"""Add jobs.attachment_notes - what came with a mail/upload but could not be read.

A password-protected zip, a damaged zip, a file type that is not read, or a zip-size limit hit
used to be dropped without a trace (a zip inside a zip was never opened at all). Now every
readable file is read (app/core/attachment_unpack.py) and whatever could not be is listed on
the job. Nullable, no backfill: older jobs simply have nothing listed.

Revision ID: u1attnotes01
Revises: t8revstart01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u1attnotes01"
down_revision = "t8revstart01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("attachment_notes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "attachment_notes")

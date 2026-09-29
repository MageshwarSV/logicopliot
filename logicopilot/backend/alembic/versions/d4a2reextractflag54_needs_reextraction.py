"""jobs.needs_reextraction - set whenever _remove_document_file (app/api/v1/jobs.py) strips
an already-extracted job's document, whether by an operator deleting the wrong file or by
custom_filter_pages.py's background sweep removing one that turned out to be entirely a
newly-uploaded junk-page reference's own content. That same fix also clears every stale
tenant CustomField value on the job instead of leaving it stranded looking valid - this
column is what tells anyone the job needs a fresh Extract afterward.

Revision ID: d4a2reextractflag54
Revises: c3f1tenantirnkey53
"""
from alembic import op
import sqlalchemy as sa

revision = "d4a2reextractflag54"
down_revision = "c3f1tenantirnkey53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("needs_reextraction", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("jobs", "needs_reextraction")

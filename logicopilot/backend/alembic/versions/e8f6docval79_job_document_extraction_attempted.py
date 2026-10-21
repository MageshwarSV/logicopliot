"""Add job_documents.extraction_attempted - Cargo Product Change Request List item 2
("Document Validation"): a document missing one of its own ask_operator_required fields is
now bounced to Unclassified Pages on its first extraction pass instead of being silently
accepted (see run_extraction in app/api/v1/jobs.py). This flag marks a slot-instance as
"already been through one pass" so a document reassigned back onto the same slot afterwards
(assign_unclassified_page) is let through even if still blank, rather than bouncing forever
with no way for the operator to accept it.

Every row that already exists is backfilled to True - it was already accepted under the old
rules before this feature shipped, and re-extracting an old job must not suddenly start
bouncing documents that were already live and in use.

Revision ID: e8f6docval79
Revises: d7e5aisub78
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa

revision = "e8f6docval79"
down_revision = "d7e5aisub78"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_documents",
        sa.Column("extraction_attempted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("job_documents", "extraction_attempted", server_default=None)


def downgrade() -> None:
    op.drop_column("job_documents", "extraction_attempted")

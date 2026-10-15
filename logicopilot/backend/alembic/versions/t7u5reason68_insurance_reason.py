"""reason_text on supporting_documents: a row created via the "upload this OR say why not"
flow (see upload_supporting_document_or_reason) keeps the operator's typed reason here instead
of a file, so it reads as the genuine answer it is on IRN Processing's own document list -
exactly the same table every other IRN-stage attachment already lives in.

Revision ID: t7u5reason68
Revises: s6t4hidecap67
"""
from alembic import op
import sqlalchemy as sa

revision = "t7u5reason68"
down_revision = "s6t4hidecap67"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supporting_documents",
        sa.Column("reason_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supporting_documents", "reason_text")

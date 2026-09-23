"""template_documents.is_required

Every document a template declares is "mandatory" by default — server_default true, so
every document that already exists (every template built before this existed) keeps
behaving exactly as it always did: a job cannot pass Document Capture without it. Turning
one off is a deliberate, per-document choice made afterwards.

Revision ID: c8f4reqdoc22
Revises: b7e3modes21
"""
import sqlalchemy as sa
from alembic import op

revision = "c8f4reqdoc22"
down_revision = "b7e3modes21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_documents",
                  sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("template_documents", "is_required")

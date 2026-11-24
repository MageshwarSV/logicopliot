"""Onboarding Step 1: the documents a Document field is read from.

erp_workbooks.documents - the customer's documents, shown as tabs in the Document bucket
(seeded from the template's own documents, more can be added); erp_fields.source_document -
which of them a Document field comes from.

Revision ID: d8erpdocs01
Revises: d7erpmap01
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa

revision = "d8erpdocs01"
down_revision = "d7erpmap01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("erp_workbooks", sa.Column("documents", sa.JSON(), nullable=True))
    op.add_column("erp_fields", sa.Column("source_document", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("erp_fields", "source_document")
    op.drop_column("erp_workbooks", "documents")

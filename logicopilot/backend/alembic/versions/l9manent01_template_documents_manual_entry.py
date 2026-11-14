"""Add template_documents.manual_entry_when_missing - "enter by hand when not uploaded".

November 5's Air Waybill is optional, but the entry still needs its values (HAWB number,
airline, flight, weights) when the customer never sends one. A document with this set gets its
tab on Required Details Review even with no file: no page image, every field empty, typeable and
not mandatory. See TemplateDocument.manual_entry_when_missing. False for every existing document.

Revision ID: l9manent01
Revises: k8dupbl01
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa

revision = "l9manent01"
down_revision = "k8dupbl01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "template_documents",
        sa.Column("manual_entry_when_missing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("template_documents", "manual_entry_when_missing")

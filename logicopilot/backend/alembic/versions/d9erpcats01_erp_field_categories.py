"""Onboarding Step 1: the six value-source categories (24 Nov 2026).

document | reference | fixed | operator | ai | formula - replacing document | calculated |
erp | contact. Existing classifications move: calculated -> formula, erp -> reference;
contact is not a value source, so those fields go back to unclassified to be sorted again.
Adds erp_fields.ai_prompt (the instruction for an AI-derived field).

Revision ID: d9erpcats01
Revises: d8erpdocs01
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa

revision = "d9erpcats01"
down_revision = "d8erpdocs01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("erp_fields", sa.Column("ai_prompt", sa.Text(), nullable=True))
    op.execute("UPDATE erp_fields SET category = 'formula' WHERE category = 'calculated'")
    op.execute("UPDATE erp_fields SET category = 'reference' WHERE category = 'erp'")
    op.execute("UPDATE erp_workbooks SET status = 'draft' WHERE id IN "
               "(SELECT DISTINCT workbook_id FROM erp_fields WHERE category = 'contact')")
    op.execute("UPDATE erp_fields SET category = NULL, source_document = NULL WHERE category = 'contact'")


def downgrade() -> None:
    op.execute("UPDATE erp_fields SET category = 'calculated' WHERE category IN ('formula', 'ai')")
    op.execute("UPDATE erp_fields SET category = 'erp' WHERE category IN ('reference', 'fixed')")
    op.execute("UPDATE erp_fields SET category = 'document' WHERE category = 'operator'")
    op.drop_column("erp_fields", "ai_prompt")

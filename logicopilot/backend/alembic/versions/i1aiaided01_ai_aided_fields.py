"""AI-aided fields: a box or field whose typed value is converted by a prompt (its condition).

field_marks.ai_aided_prompt / custom_fields.ai_aided_prompt - null = an ordinary field. When set,
whatever an operator types and saves is converted by this prompt (e.g. "Port Name (UN/LOCODE)")
before it is stored - see app/api/v1/jobs.py correct_field_value.

Revision ID: i1aiaided01
Revises: h4erprules01
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "i1aiaided01"
down_revision = "h4erprules01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("field_marks", sa.Column("ai_aided_prompt", sa.Text(), nullable=True))
    op.add_column("custom_fields", sa.Column("ai_aided_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_fields", "ai_aided_prompt")
    op.drop_column("field_marks", "ai_aided_prompt")

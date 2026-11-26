"""Remembered AI answers from the Indian Customs Tariff, per tenant and line wording.

Revision ID: g2cthsuggest01
Revises: g1custtariff01
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "g2cthsuggest01"
down_revision = "g1custtariff01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cth_tariff_suggestions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("line_key", sa.String(64), nullable=False),
        sa.Column("doc_cth", sa.String(40), nullable=True),
        sa.Column("part_code", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cth", sa.String(8), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cth_tariff_suggestions_tenant_id", "cth_tariff_suggestions", ["tenant_id"])
    op.create_index("ix_cth_tariff_suggestions_line_key", "cth_tariff_suggestions", ["line_key"])


def downgrade() -> None:
    op.drop_index("ix_cth_tariff_suggestions_line_key", table_name="cth_tariff_suggestions")
    op.drop_index("ix_cth_tariff_suggestions_tenant_id", table_name="cth_tariff_suggestions")
    op.drop_table("cth_tariff_suggestions")

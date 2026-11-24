"""Customer Onboarding Step 2 (Document Mapping): one sample per customer document, and one
field box per Document field. New tables only.

Revision ID: e1erpdocmap01
Revises: d9erpcats01
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa

revision = "e1erpdocmap01"
down_revision = "d9erpcats01"
branch_labels = None
depends_on = None


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "erp_doc_samples",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workbook_id", sa.String(length=36),
                  sa.ForeignKey("erp_workbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("stored_dir", sa.String(length=1024), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_name", sa.String(length=255), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("workbook_id", "doc_id", name="uq_erp_doc_samples_workbook_doc"),
    )
    op.create_index("ix_erp_doc_samples_workbook_id", "erp_doc_samples", ["workbook_id"])
    op.create_table(
        "erp_field_marks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workbook_id", sa.String(length=36),
                  sa.ForeignKey("erp_workbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_id", sa.String(length=36),
                  sa.ForeignKey("erp_fields.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("line_item", sa.Boolean(), nullable=False),
        sa.Column("detected_value", sa.Text(), nullable=True),
        sa.Column("anchor", sa.Text(), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("field_id", name="uq_erp_field_marks_field_id"),
    )
    op.create_index("ix_erp_field_marks_workbook_id", "erp_field_marks", ["workbook_id"])


def downgrade() -> None:
    op.drop_index("ix_erp_field_marks_workbook_id", table_name="erp_field_marks")
    op.drop_table("erp_field_marks")
    op.drop_index("ix_erp_doc_samples_workbook_id", table_name="erp_doc_samples")
    op.drop_table("erp_doc_samples")

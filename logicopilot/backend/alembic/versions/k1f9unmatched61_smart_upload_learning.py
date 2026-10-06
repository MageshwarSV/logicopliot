"""unmatched_upload_pages / classification_examples - a page smart-upload could not place is
kept instead of silently discarded; an operator's manual drag-and-drop assignment is remembered
as a classification example so a future similarly-worded document is recognised on its own.

Revision ID: k1f9unmatched61
Revises: j0e8standalonehdg60
"""
from alembic import op
import sqlalchemy as sa

revision = "k1f9unmatched61"
down_revision = "j0e8standalonehdg60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unmatched_upload_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_unmatched_upload_pages_tenant_id", "unmatched_upload_pages", ["tenant_id"])
    op.create_index("ix_unmatched_upload_pages_job_id", "unmatched_upload_pages", ["job_id"])

    op.create_table(
        "classification_examples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("group_id", sa.String(36), sa.ForeignKey("template_groups.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("template_document_id", sa.String(36),
                 sa.ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("snippet_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"),
                 nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_classification_examples_tenant_id", "classification_examples", ["tenant_id"])
    op.create_index("ix_classification_examples_group_id", "classification_examples", ["group_id"])
    op.create_index("ix_classification_examples_template_document_id",
                    "classification_examples", ["template_document_id"])


def downgrade() -> None:
    op.drop_table("classification_examples")
    op.drop_table("unmatched_upload_pages")

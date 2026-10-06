"""unmatched_upload_pages - a page smart-upload could not confidently place is kept instead
of silently discarded, so an operator can resolve it by hand (see the drag-and-drop resolver
in JobRunPage.tsx). The learning side of this lives in document_samples/DocumentSample
(j9d7docsamples59) instead of a separate table here - assigning one of these pages calls
document_samples.remember() the same way an ordinary manual upload already does.

Revision ID: k1f9unmatched61
Revises: j9d7docsamples59
"""
from alembic import op
import sqlalchemy as sa

revision = "k1f9unmatched61"
down_revision = "j9d7docsamples59"
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


def downgrade() -> None:
    op.drop_table("unmatched_upload_pages")

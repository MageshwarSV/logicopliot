"""custom_filter_pages — Super Admin content-match page filter (global, uploaded reference
pages OCR'd once; document pages compared to them by pure difflib string similarity, never
an AI call)

Revision ID: x1u5filterpg43
Revises: w0t4openaiadmin42
"""
from alembic import op
import sqlalchemy as sa

revision = "x1u5filterpg43"
down_revision = "w0t4openaiadmin42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_filter_pages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("reference_text", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("custom_filter_pages")

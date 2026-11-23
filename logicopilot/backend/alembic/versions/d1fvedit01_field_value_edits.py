"""Add field_value_edits - every operator edit of an extracted field (before / after / AI value).

Feeds Super Admin -> Extraction Corrections. Nothing is back-filled: before this, only the
latest corrected value was kept (job_field_values.corrected_value), and that screen reads it
directly for older jobs.

Revision ID: d1fvedit01
Revises: c1invpay01
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "d1fvedit01"
down_revision = "c1invpay01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "field_value_edits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("job_reference", sa.String(length=255), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=True),
        sa.Column("template_document_id", sa.String(length=36), nullable=True),
        sa.Column("job_document_id", sa.String(length=36), nullable=True),
        sa.Column("mark_id", sa.String(length=36), nullable=True),
        sa.Column("custom_field_id", sa.String(length=36), nullable=True),
        sa.Column("field_value_id", sa.String(length=36), nullable=True),
        sa.Column("label_name", sa.String(length=100), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column("set_index", sa.Integer(), nullable=True),
        sa.Column("ai_value", sa.Text(), nullable=True),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="edit"),
        sa.Column("edited_by_id", sa.String(length=36), nullable=True),
        sa.Column("edited_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("tenant_id", "job_id", "group_id", "field_value_id", "label_name"):
        op.create_index(f"ix_field_value_edits_{col}", "field_value_edits", [col])


def downgrade() -> None:
    for col in ("tenant_id", "job_id", "group_id", "field_value_id", "label_name"):
        op.drop_index(f"ix_field_value_edits_{col}", table_name="field_value_edits")
    op.drop_table("field_value_edits")

"""custom (computed/hardcoded) fields + nullable mark/tdoc on job_field_values

Revision ID: a1b4customfields03
Revises: a1b3jobstage02
Create Date: 2026-07-19
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b4customfields03"
down_revision: Union[str, Sequence[str], None] = "a1b3jobstage02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_fields",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("group_id", sa.String(length=36), sa.ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("label_name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="ai"),
        sa.Column("hardcoded_value", sa.Text(), nullable=True),
        sa.Column("ai_prompt", sa.Text(), nullable=True),
        sa.Column("source_document_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    # job_field_values: make mark_id / template_document_id nullable + add custom_field_id.
    with op.batch_alter_table("job_field_values") as b:
        b.alter_column("mark_id", existing_type=sa.String(length=36), nullable=True)
        b.alter_column("template_document_id", existing_type=sa.String(length=36), nullable=True)
        b.add_column(sa.Column("custom_field_id", sa.String(length=36), sa.ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_job_field_values_custom_field_id", "job_field_values", ["custom_field_id"])


def downgrade() -> None:
    op.drop_index("ix_job_field_values_custom_field_id", table_name="job_field_values")
    with op.batch_alter_table("job_field_values") as b:
        b.drop_column("custom_field_id")
    op.drop_table("custom_fields")

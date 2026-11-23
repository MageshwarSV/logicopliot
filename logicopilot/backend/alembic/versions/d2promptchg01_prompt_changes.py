"""Add prompt_changes - history of field prompt / format hint changes made from the Fix screen
(Super Admin -> Extraction Corrections), so each one can be reviewed and reverted.

Revision ID: d2promptchg01
Revises: d1fvedit01
Create Date: 2026-11-23
"""

from alembic import op
import sqlalchemy as sa

revision = "d2promptchg01"
down_revision = "d1fvedit01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=True),
        sa.Column("group_id", sa.String(length=36), nullable=True),
        sa.Column("mark_id", sa.String(length=36), nullable=True),
        sa.Column("custom_field_id", sa.String(length=36), nullable=True),
        sa.Column("label_name", sa.String(length=100), nullable=False),
        sa.Column("old_prompt", sa.Text(), nullable=True),
        sa.Column("new_prompt", sa.Text(), nullable=True),
        sa.Column("old_format_hint", sa.Text(), nullable=True),
        sa.Column("new_format_hint", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False, server_default="apply"),
        sa.Column("changed_by_id", sa.String(length=36), nullable=True),
        sa.Column("changed_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("tenant_id", "mark_id", "custom_field_id"):
        op.create_index(f"ix_prompt_changes_{col}", "prompt_changes", [col])


def downgrade() -> None:
    for col in ("tenant_id", "mark_id", "custom_field_id"):
        op.drop_index(f"ix_prompt_changes_{col}", table_name="prompt_changes")
    op.drop_table("prompt_changes")

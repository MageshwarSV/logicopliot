"""Add erp_workbooks, erp_fields, erp_mapping_history - Customer Onboarding Step 1
(ERP Field Classification). New tables only; the older Excel entry setup is untouched.

Revision ID: d7erpmap01
Revises: d6scrlay01
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa

revision = "d7erpmap01"
down_revision = "d6scrlay01"
branch_labels = None
depends_on = None


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "erp_workbooks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36),
                  sa.ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=1024), nullable=False),
        sa.Column("sheets", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("uploaded_by_name", sa.String(length=255), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("group_id", name="uq_erp_workbooks_group_id"),
    )
    op.create_index("ix_erp_workbooks_tenant_id", "erp_workbooks", ["tenant_id"])

    op.create_table(
        "erp_fields",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workbook_id", sa.String(length=36),
                  sa.ForeignKey("erp_workbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("column_key", sa.String(length=10), nullable=True),
        sa.Column("column_index", sa.Integer(), nullable=True),
        sa.Column("excel_name", sa.String(length=255), nullable=False),
        sa.Column("is_custom", sa.Boolean(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("display_label", sa.String(length=255), nullable=True),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("validation", sa.Text(), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("erp_master_ref", sa.Text(), nullable=True),
        sa.Column("contact_ref", sa.Text(), nullable=True),
        sa.Column("samples", sa.JSON(), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_erp_fields_workbook_id", "erp_fields", ["workbook_id"])
    op.create_index("ix_erp_fields_group_id", "erp_fields", ["group_id"])

    op.create_table(
        "erp_mapping_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workbook_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("field_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_erp_mapping_history_workbook_id", "erp_mapping_history", ["workbook_id"])
    op.create_index("ix_erp_mapping_history_group_id", "erp_mapping_history", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_erp_mapping_history_group_id", table_name="erp_mapping_history")
    op.drop_index("ix_erp_mapping_history_workbook_id", table_name="erp_mapping_history")
    op.drop_table("erp_mapping_history")
    op.drop_index("ix_erp_fields_group_id", table_name="erp_fields")
    op.drop_index("ix_erp_fields_workbook_id", table_name="erp_fields")
    op.drop_table("erp_fields")
    op.drop_index("ix_erp_workbooks_tenant_id", table_name="erp_workbooks")
    op.drop_table("erp_workbooks")

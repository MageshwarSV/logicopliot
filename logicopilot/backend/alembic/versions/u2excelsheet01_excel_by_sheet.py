"""Excel by sheet: template switch + ignore list, Excel documents, Unclassified Excel files.

* template_groups.auto_extract (true for every existing template - extraction starts by
  itself as before; off = classify and place only, extraction waits for Extract).
* template_groups.excel_mode ("legacy" for every existing template - nothing changes until a
  template is switched to "sheet") and template_groups.excel_ignore_patterns.
* job_documents.source_kind ("pdf" for every existing row) and job_documents.sheet_name.
* unclassified_files: Excel workbooks/sheets not placed in a slot, assignable by an operator.

Revision ID: u2excelsheet01
Revises: u1attnotes01
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "u2excelsheet01"
down_revision = "u1attnotes01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("excel_mode", sa.String(10), nullable=False, server_default="legacy"))
    op.add_column("template_groups", sa.Column("excel_ignore_patterns", sa.JSON(), nullable=True))
    op.add_column("template_groups", sa.Column("auto_extract", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("job_documents", sa.Column("source_kind", sa.String(10), nullable=False, server_default="pdf"))
    op.add_column("job_documents", sa.Column("sheet_name", sa.String(255), nullable=True))
    op.create_table(
        "unclassified_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("sheets", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_unclassified_files_tenant_id", "unclassified_files", ["tenant_id"])
    op.create_index("ix_unclassified_files_job_id", "unclassified_files", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_unclassified_files_job_id", table_name="unclassified_files")
    op.drop_index("ix_unclassified_files_tenant_id", table_name="unclassified_files")
    op.drop_table("unclassified_files")
    op.drop_column("job_documents", "sheet_name")
    op.drop_column("job_documents", "source_kind")
    op.drop_column("template_groups", "auto_extract")
    op.drop_column("template_groups", "excel_ignore_patterns")
    op.drop_column("template_groups", "excel_mode")

"""Add custom_fields.fixed_options and custom_fields.display_document_id - the two columns a
"Manual Entry" field (created straight from the ERP Script Recorder) needs that no existing
custom field shape already covers: a closed dropdown of admin-typed choices instead of free
text, and which document's own tab to show up under instead of always Additional Details.

Both additive and nullable - every existing custom field keeps behaving exactly as it does
today (free text, Additional Details) with no backfill needed.

Revision ID: v2c7manent1
Revises: u1h5appmgr3
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa

revision = "v2c7manent1"
down_revision = "u1h5appmgr3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_fields", sa.Column("fixed_options", sa.JSON(), nullable=True))
    op.add_column(
        "custom_fields",
        sa.Column("display_document_id", sa.String(length=36), nullable=True),
    )
    with op.batch_alter_table("custom_fields") as batch:
        batch.create_foreign_key(
            "fk_custom_fields_display_document_id",
            "template_documents", ["display_document_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("custom_fields") as batch:
        batch.drop_constraint("fk_custom_fields_display_document_id", type_="foreignkey")
    op.drop_column("custom_fields", "display_document_id")
    op.drop_column("custom_fields", "fixed_options")

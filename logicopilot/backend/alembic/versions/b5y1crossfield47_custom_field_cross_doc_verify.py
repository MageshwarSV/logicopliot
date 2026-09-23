"""cross_doc_links.source_custom_field_id — a custom field (not just a mark) can now be the
SOURCE of a cross-document verification link, e.g. an AI-computed "Total Amount (Calculated)"
checked against the Invoice's own "Total" mark on another document. source_mark_id becomes
nullable; exactly one of the two must be set. The TARGET side stays mark-only, on purpose - a
custom field has no page/position to compare against, only a value, so it never makes sense as
a target. Also custom_fields.verify_with_other_document, mirroring field_marks' own column -
the tick that starts the process, kept even before any target mark is picked.

Revision ID: b5y1crossfield47
Revises: a4x9irndocs46
"""
from alembic import op
import sqlalchemy as sa

revision = "b5y1crossfield47"
down_revision = "a4x9irndocs46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_fields",
        sa.Column("verify_with_other_document", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("cross_doc_links") as b:
        b.alter_column("source_mark_id", existing_type=sa.String(length=36), nullable=True)
        b.add_column(
            sa.Column("source_custom_field_id", sa.String(length=36),
                      sa.ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=True)
        )
        b.create_check_constraint(
            "ck_cross_doc_links_one_source",
            "(source_mark_id IS NOT NULL AND source_custom_field_id IS NULL) OR "
            "(source_mark_id IS NULL AND source_custom_field_id IS NOT NULL)",
        )
    op.create_index(
        "ix_cross_doc_links_source_custom_field_id", "cross_doc_links", ["source_custom_field_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_cross_doc_links_source_custom_field_id", table_name="cross_doc_links")
    with op.batch_alter_table("cross_doc_links") as b:
        b.drop_constraint("ck_cross_doc_links_one_source", type_="check")
        b.drop_column("source_custom_field_id")
        b.alter_column("source_mark_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_column("custom_fields", "verify_with_other_document")

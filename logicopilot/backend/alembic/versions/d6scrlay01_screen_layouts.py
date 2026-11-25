"""Add screen_layouts + screen_layout_history - Super Admin -> Screen Designer.

A template document's job-screen layout (sections, their order, the product card). Opt-in:
nothing is back-filled, so every existing template keeps its current screen until a layout
is saved for it.

Revision ID: d6scrlay01
Revises: d5brsuser01
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa

revision = "d6scrlay01"
down_revision = "d5brsuser01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screen_layouts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("template_document_id", sa.String(length=36),
                  sa.ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=False),
        sa.Column("updated_by_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_document_id", name="uq_screen_layouts_template_document_id"),
    )
    op.create_index("ix_screen_layouts_tenant_id", "screen_layouts", ["tenant_id"])
    op.create_index("ix_screen_layouts_group_id", "screen_layouts", ["group_id"])

    op.create_table(
        "screen_layout_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("template_document_id", sa.String(length=36),
                  sa.ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=True),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("saved_by_id", sa.String(length=36), nullable=True),
        sa.Column("saved_by_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_screen_layout_history_template_document_id", "screen_layout_history",
                    ["template_document_id"])


def downgrade() -> None:
    op.drop_index("ix_screen_layout_history_template_document_id", table_name="screen_layout_history")
    op.drop_table("screen_layout_history")
    op.drop_index("ix_screen_layouts_group_id", table_name="screen_layouts")
    op.drop_index("ix_screen_layouts_tenant_id", table_name="screen_layouts")
    op.drop_table("screen_layouts")

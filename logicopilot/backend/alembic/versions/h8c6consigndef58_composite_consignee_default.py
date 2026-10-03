"""composite_field_consignee_defaults - remembers a composite field's piece ORDER per
consignee (field-reference pieces only, never a fixed-value piece), so a new job for the
SAME consignee pre-fills with, and computes using, that consignee's own remembered order
instead of the template's generic fallback.

Revision ID: h8c6consigndef58
Revises: g7b5compfield57
"""
from alembic import op
import sqlalchemy as sa

revision = "h8c6consigndef58"
down_revision = "g7b5compfield57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "composite_field_consignee_defaults",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("custom_field_id", sa.String(36), sa.ForeignKey("custom_fields.id", ondelete="CASCADE"),
                 nullable=False),
        sa.Column("consignee_key", sa.String(255), nullable=False),
        sa.Column("source_labels", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("custom_field_id", "consignee_key", name="uq_composite_consignee_default"),
    )
    op.create_index(
        "ix_composite_field_consignee_defaults_tenant_id",
        "composite_field_consignee_defaults", ["tenant_id"],
    )
    op.create_index(
        "ix_composite_field_consignee_defaults_custom_field_id",
        "composite_field_consignee_defaults", ["custom_field_id"],
    )
    op.create_index(
        "ix_composite_field_consignee_defaults_consignee_key",
        "composite_field_consignee_defaults", ["consignee_key"],
    )


def downgrade() -> None:
    op.drop_table("composite_field_consignee_defaults")

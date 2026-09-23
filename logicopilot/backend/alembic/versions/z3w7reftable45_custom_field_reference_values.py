"""custom_field_reference_values — learned lookup cache: what a kind="lookup" custom field
resolved to, the one time an operator typed it in because the material master had nothing
for that line, so the same line next time is filled automatically instead of asked again.

Revision ID: z3w7reftable45
Revises: y2v6openaibalance44
"""
from alembic import op
import sqlalchemy as sa

revision = "z3w7reftable45"
down_revision = "y2v6openaibalance44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_field_reference_values",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "custom_field_id", sa.String(length=36),
            sa.ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("match_value_1", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("match_value_2", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("match_value_3", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("match_value_4", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("resolved_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "custom_field_id", "match_value_1", "match_value_2", "match_value_3", "match_value_4",
            name="uq_custom_field_reference_values_key",
        ),
    )
    op.create_index(
        "ix_custom_field_reference_values_custom_field_id",
        "custom_field_reference_values", ["custom_field_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_field_reference_values_custom_field_id",
                   table_name="custom_field_reference_values")
    op.drop_table("custom_field_reference_values")

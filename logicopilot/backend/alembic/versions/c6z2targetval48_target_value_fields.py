""""Target value" fields: a mark or custom field ticked this way is extracted normally, then
its own raw value is looked up in a reference table keyed on ITSELF (not a derived multi-column
key the way kind="lookup" custom fields already work) - a match returns the reference table's
stored value, no match returns empty rather than the raw extraction. field_marks and
custom_fields each get an is_target_value flag; custom_field_reference_values gains a nullable
mark_id alongside its existing (now nullable) custom_field_id, so the same learned-value table
backs both kinds of field instead of building a second one. job_field_values gains
target_value_raw: what the field actually extracted BEFORE the lookup replaced/blanked it,
kept so a later operator correction can still be remembered against the right key even though
extracted_value itself was already blanked out for the "no match" case.

Revision ID: c6z2targetval48
Revises: b5y1crossfield47
"""
from alembic import op
import sqlalchemy as sa

revision = "c6z2targetval48"
down_revision = "b5y1crossfield47"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "field_marks",
        sa.Column("is_target_value", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "custom_fields",
        sa.Column("is_target_value", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("custom_field_reference_values") as b:
        b.alter_column("custom_field_id", existing_type=sa.String(length=36), nullable=True)
        b.add_column(
            sa.Column("mark_id", sa.String(length=36),
                      sa.ForeignKey("field_marks.id", ondelete="CASCADE"), nullable=True)
        )
        b.create_check_constraint(
            "ck_custom_field_reference_values_one_owner",
            "(custom_field_id IS NOT NULL AND mark_id IS NULL) OR "
            "(custom_field_id IS NULL AND mark_id IS NOT NULL)",
        )
        b.drop_constraint("uq_custom_field_reference_values_key", type_="unique")
        b.create_unique_constraint(
            "uq_custom_field_reference_values_key",
            ["custom_field_id", "mark_id", "match_value_1", "match_value_2", "match_value_3", "match_value_4"],
        )
    op.create_index(
        "ix_custom_field_reference_values_mark_id", "custom_field_reference_values", ["mark_id"],
    )
    op.add_column(
        "job_field_values",
        sa.Column("target_value_raw", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_field_values", "target_value_raw")
    op.drop_index("ix_custom_field_reference_values_mark_id", table_name="custom_field_reference_values")
    with op.batch_alter_table("custom_field_reference_values") as b:
        b.drop_constraint("uq_custom_field_reference_values_key", type_="unique")
        b.create_unique_constraint(
            "uq_custom_field_reference_values_key",
            ["custom_field_id", "match_value_1", "match_value_2", "match_value_3", "match_value_4"],
        )
        b.drop_constraint("ck_custom_field_reference_values_one_owner", type_="check")
        b.drop_column("mark_id")
        b.alter_column("custom_field_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_column("custom_fields", "is_target_value")
    op.drop_column("field_marks", "is_target_value")

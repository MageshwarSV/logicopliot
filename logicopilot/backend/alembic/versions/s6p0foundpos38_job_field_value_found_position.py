"""job_field_values found_page/x/y/width/height — where a value's own text was actually
found on the real uploaded document (OCR word-box match), separate from FieldMark's static
template-drawn box.

Revision ID: s6p0foundpos38
Revises: r5o9writetoggle37
"""
from alembic import op
import sqlalchemy as sa

revision = "s6p0foundpos38"
down_revision = "r5o9writetoggle37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_field_values", sa.Column("found_page", sa.Integer(), nullable=True))
    op.add_column("job_field_values", sa.Column("found_x", sa.Float(), nullable=True))
    op.add_column("job_field_values", sa.Column("found_y", sa.Float(), nullable=True))
    op.add_column("job_field_values", sa.Column("found_width", sa.Float(), nullable=True))
    op.add_column("job_field_values", sa.Column("found_height", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_field_values", "found_height")
    op.drop_column("job_field_values", "found_width")
    op.drop_column("job_field_values", "found_y")
    op.drop_column("job_field_values", "found_x")
    op.drop_column("job_field_values", "found_page")

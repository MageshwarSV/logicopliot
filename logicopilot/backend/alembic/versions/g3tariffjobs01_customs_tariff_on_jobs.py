"""Indian Customs Tariff on jobs: the per-template switch (off by default, so no template
changes) and where a job value came from.

Revision ID: g3tariffjobs01
Revises: g2cthsuggest01
Create Date: 2026-11-26
"""

from alembic import op
import sqlalchemy as sa

revision = "g3tariffjobs01"
down_revision = "g2cthsuggest01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("template_groups", sa.Column("use_customs_tariff", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))
    op.add_column("job_field_values", sa.Column("value_source", sa.String(20), nullable=True))
    op.add_column("job_field_values", sa.Column("source_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_field_values", "source_note")
    op.drop_column("job_field_values", "value_source")
    op.drop_column("template_groups", "use_customs_tariff")

"""Add field_marks.full_width and the same column on custom_fields - an admin-settable
"span the full row in the job screen's two-column field grid" toggle, through the Template
Creation Wizard, replacing the FULL_WIDTH_FIELD_LABELS hardcoded list in JobRunPage.tsx (that
list stays in place as a fallback for the handful of fields already relying on it, so nothing
already full-width regresses).

Additive and nullable-default-false - every existing field keeps rendering half-width exactly
as it does today (the hardcoded list's own fields keep rendering full-width unchanged too).

Revision ID: b8r2fullwd1
Revises: a7p1validt1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "b8r2fullwd1"
down_revision = "a7p1validt1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column("full_width", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "full_width")

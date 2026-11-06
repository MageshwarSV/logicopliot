"""Add field_marks.multiline and the same column on custom_fields - an admin-settable
"render as a multi-line, auto-growing box" toggle, through the Template Creation Wizard,
replacing the MULTILINE_FIELD_LABELS hardcoded list in JobRunPage.tsx (that list stays in
place as a fallback for the handful of fields already relying on it).

Additive and nullable-default-false - every existing field keeps rendering single-line exactly
as it does today (the hardcoded list's own fields keep rendering multi-line unchanged too).

Revision ID: c9s3mline1
Revises: b8r2fullwd1
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa

revision = "c9s3mline1"
down_revision = "b8r2fullwd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.add_column(
            table,
            sa.Column("multiline", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    for table in ("field_marks", "custom_fields"):
        op.drop_column(table, "multiline")

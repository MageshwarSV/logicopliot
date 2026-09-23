"""assigned modes rename — users.gk2_modes -> users.assigned_modes

Both gk2 (a real access gate) and operator (a new tab-filter convenience only — their real
access control stays UserTemplateAssignment, untouched) now need a "which modes" field, so
the column becomes generic. A pure catalog rename on Postgres — no data touched, no type
change, existing GK2 users' modes survive exactly as they were.

Revision ID: o2l6modesrenamed34
Revises: n1k5gk2reviewsep33
"""
from alembic import op

revision = "o2l6modesrenamed34"
down_revision = "n1k5gk2reviewsep33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "gk2_modes", new_column_name="assigned_modes")


def downgrade() -> None:
    op.alter_column("users", "assigned_modes", new_column_name="gk2_modes")

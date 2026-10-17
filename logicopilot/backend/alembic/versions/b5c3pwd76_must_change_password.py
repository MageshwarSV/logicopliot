"""Add users.must_change_password - forces a one-time "set your own password" popup after an
admin (Super Admin or Tenant Admin) creates an account or resets its password. Defaults false
so no existing account is retroactively forced to change a password already in use.

Revision ID: b5c3pwd76
Revises: a4b2erp75
Create Date: 2026-10-17
"""

from alembic import op
import sqlalchemy as sa

revision = "b5c3pwd76"
down_revision = "a4b2erp75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")

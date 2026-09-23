"""tenant allowed_modes, template group mode

A client is licensed for certain transport modes (Sea Import, Air Export, ...) and a
template is built for exactly one of them. Super Admin sets the tenant's allowed modes at
creation; template creation then only offers those, and tags the template with the one
chosen. Both are nullable/empty by default so every existing tenant and template keeps
working exactly as it did — the restriction only takes effect once a tenant actually has
allowed_modes set.

Revision ID: b7e3modes21
Revises: a6d2approve20
"""
import sqlalchemy as sa
from alembic import op

revision = "b7e3modes21"
down_revision = "a6d2approve20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("allowed_modes", sa.JSON(), nullable=True))
    op.add_column("template_groups", sa.Column("mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("template_groups", "mode")
    op.drop_column("tenants", "allowed_modes")

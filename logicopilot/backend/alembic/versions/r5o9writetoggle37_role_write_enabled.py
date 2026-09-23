"""tenant role_write_enabled — per-tenant read-only lock for operator/gk2

Set from the Tenant Admin's "Masters" page. A missing key or a null column means
read-and-write, so every tenant that existed before this was added is unaffected.

Revision ID: r5o9writetoggle37
Revises: p3m7managerrole35
"""
from alembic import op
import sqlalchemy as sa

revision = "r5o9writetoggle37"
down_revision = "p3m7managerrole35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("role_write_enabled", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "role_write_enabled")

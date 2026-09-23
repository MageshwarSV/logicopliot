"""gk2 role — users.role widened to include "gk2", users.gk2_modes added

Gate Keeper 2: a tenant-scoped second sign-off role, created by the Tenant Admin like an
operator. Widens ck_users_role_valid the same way d1a5admin23 widened it for "admin". The
tenant-scope constraint is untouched — gk2 already falls under "role NOT IN (super_admin,
admin)", so it already requires tenant_id NOT NULL, exactly as it should.

Revision ID: m0j4gk2role32
Revises: l9i3gk2status31
"""
from alembic import op
import sqlalchemy as sa

revision = "m0j4gk2role32"
down_revision = "l9i3gk2status31"
branch_labels = None
depends_on = None

ROLE_CONSTRAINT = "ck_users_role_valid"


def upgrade() -> None:
    op.add_column("users", sa.Column("gk2_modes", sa.JSON(), nullable=True))
    op.drop_constraint(ROLE_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        ROLE_CONSTRAINT, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin', 'gk2')",
    )


def downgrade() -> None:
    op.drop_constraint(ROLE_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        ROLE_CONSTRAINT, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin')",
    )
    op.drop_column("users", "gk2_modes")

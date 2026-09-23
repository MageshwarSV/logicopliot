"""manager role — users.role widened to include "manager"

Tenant-scoped, read-only observer over their own tenant's whole operation. Widens
ck_users_role_valid the same way d1a5admin23/m0j4gk2role32 did for admin/gk2. The
tenant-scope constraint is untouched — manager already falls under "role NOT IN
(super_admin, admin)", so it already requires tenant_id NOT NULL, exactly as it should.

Revision ID: p3m7managerrole35
Revises: o2l6modesrenamed34
"""
from alembic import op

revision = "p3m7managerrole35"
down_revision = "o2l6modesrenamed34"
branch_labels = None
depends_on = None

ROLE_CONSTRAINT = "ck_users_role_valid"


def upgrade() -> None:
    op.drop_constraint(ROLE_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        ROLE_CONSTRAINT, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin', 'gk2', 'manager')",
    )


def downgrade() -> None:
    op.drop_constraint(ROLE_CONSTRAINT, "users", type_="check")
    op.create_check_constraint(
        ROLE_CONSTRAINT, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin', 'gk2')",
    )

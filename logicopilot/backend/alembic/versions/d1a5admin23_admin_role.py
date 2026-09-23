"""admin role — cross-tenant, jobs-only, tenant_id NULL like super_admin

Widens the two CHECK constraints on users: the valid-role list now includes "admin", and
the tenant-scope rule now says tenant_id must be NULL for super_admin OR admin (was
super_admin alone) and NOT NULL for everyone else. No existing row is affected — every
row already satisfies both new constraints, since none of them has role='admin' yet.

Revision ID: d1a5admin23
Revises: c8f4reqdoc22
"""
from alembic import op

revision = "d1a5admin23"
down_revision = "c8f4reqdoc22"
branch_labels = None
depends_on = None

OLD_ROLE = "ck_users_role_valid"
OLD_SCOPE = "ck_users_tenant_scope"


def upgrade() -> None:
    op.drop_constraint(OLD_ROLE, "users", type_="check")
    op.drop_constraint(OLD_SCOPE, "users", type_="check")
    op.create_check_constraint(
        OLD_ROLE, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin')",
    )
    op.create_check_constraint(
        OLD_SCOPE, "users",
        "(role IN ('super_admin', 'admin') AND tenant_id IS NULL) OR "
        "(role NOT IN ('super_admin', 'admin') AND tenant_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(OLD_SCOPE, "users", type_="check")
    op.drop_constraint(OLD_ROLE, "users", type_="check")
    op.create_check_constraint(
        OLD_ROLE, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator')",
    )
    op.create_check_constraint(
        OLD_SCOPE, "users",
        "(role = 'super_admin' AND tenant_id IS NULL) OR "
        "(role != 'super_admin' AND tenant_id IS NOT NULL)",
    )

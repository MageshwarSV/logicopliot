"""saas_finance role — cross-tenant, read-only finance/billing view, tenant_id NULL like
super_admin/admin

Widens the two CHECK constraints on users the same way admin's own migration
(d1a5admin23) did: the valid-role list now includes "saas_finance", and the tenant-scope
rule now says tenant_id must be NULL for super_admin/admin/saas_finance (was super_admin/
admin) and NOT NULL for everyone else. No existing row is affected — none has
role='saas_finance' yet.

Revision ID: s9f3finrole1
Revises: g4b1rul81
"""
from alembic import op

revision = "s9f3finrole1"
down_revision = "g4b1rul81"
branch_labels = None
depends_on = None

OLD_ROLE = "ck_users_role_valid"
OLD_SCOPE = "ck_users_tenant_scope"


def upgrade() -> None:
    op.drop_constraint(OLD_ROLE, "users", type_="check")
    op.drop_constraint(OLD_SCOPE, "users", type_="check")
    op.create_check_constraint(
        OLD_ROLE, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin', 'gk2', 'manager', "
        "'saas_finance')",
    )
    op.create_check_constraint(
        OLD_SCOPE, "users",
        "(role IN ('super_admin', 'admin', 'saas_finance') AND tenant_id IS NULL) OR "
        "(role NOT IN ('super_admin', 'admin', 'saas_finance') AND tenant_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(OLD_SCOPE, "users", type_="check")
    op.drop_constraint(OLD_ROLE, "users", type_="check")
    op.create_check_constraint(
        OLD_ROLE, "users",
        "role IN ('super_admin', 'tenant_admin', 'operator', 'admin', 'gk2', 'manager')",
    )
    op.create_check_constraint(
        OLD_SCOPE, "users",
        "(role IN ('super_admin', 'admin') AND tenant_id IS NULL) OR "
        "(role NOT IN ('super_admin', 'admin') AND tenant_id IS NOT NULL)",
    )

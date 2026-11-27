"""Allow the support role - Production Support, cross-tenant like admin.

Both user CHECK constraints change: the role list, and ck_users_tenant_scope (support has no
tenant, like super_admin / admin / saas_finance).

Revision ID: h2support01
Revises: h1erphelp01
Create Date: 2026-11-27
"""

from alembic import op
import sqlalchemy as sa

revision = "h2support01"
down_revision = "h1erphelp01"
branch_labels = None
depends_on = None

# Spelled out, not imported: a migration describes the schema as it was at this point.
OLD_ROLES = ("super_admin", "tenant_admin", "operator", "admin", "gk2", "manager", "saas_finance", "app_manager")
NEW_ROLES = OLD_ROLES + ("support",)
OLD_CROSS = ("super_admin", "admin", "saas_finance")
NEW_CROSS = OLD_CROSS + ("support",)


def _swap(roles: tuple[str, ...], cross: tuple[str, ...]) -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role_valid", type_="check")
        batch.create_check_constraint("ck_users_role_valid", sa.text(f"role IN {roles}"))
        batch.drop_constraint("ck_users_tenant_scope", type_="check")
        batch.create_check_constraint(
            "ck_users_tenant_scope",
            sa.text(f"(role IN {cross} AND tenant_id IS NULL) OR "
                    f"(role NOT IN {cross} AND tenant_id IS NOT NULL)"),
        )


def upgrade() -> None:
    _swap(NEW_ROLES, NEW_CROSS)


def downgrade() -> None:
    # A support login owns no job, template or mailbox - removing it orphans nothing.
    op.execute("DELETE FROM users WHERE role = 'support'")
    _swap(OLD_ROLES, OLD_CROSS)

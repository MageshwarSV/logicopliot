"""Allow the app_manager role - the native Manager mobile app's own login.

A separate role from `manager` on purpose. A Manager is defined as seeing their tenant's
whole operation unrestricted by mode; these logins are the opposite, with one company taking
several of them, each limited to the shipment types that login watches (one for Sea Import,
another for Air Import). Widening `manager` to carry that restriction would have changed what
every existing Manager account can already see.

Only the role CHECK constraint changes. app_manager is tenant-scoped, so the existing
ck_users_tenant_scope (which requires a tenant_id for every role outside super_admin/admin/
saas_finance) already covers it correctly and is left alone.

Revision ID: u1h5appmgr3
Revises: t0g4jobcomp2
Create Date: 2026-10-27
"""

from alembic import op
import sqlalchemy as sa

revision = "u1h5appmgr3"
down_revision = "t0g4jobcomp2"
branch_labels = None
depends_on = None

# Spelled out rather than imported from app.models.user: a migration has to keep describing
# the schema as it was at THIS point in history, and importing the live tuple would silently
# rewrite this migration's meaning the next time a role is added.
OLD_ROLES = ("super_admin", "tenant_admin", "operator", "admin", "gk2", "manager", "saas_finance")
NEW_ROLES = OLD_ROLES + ("app_manager",)


def _swap_role_check(roles: tuple[str, ...]) -> None:
    """SQLite cannot ALTER a CHECK constraint, so this runs inside a batch operation, which
    rebuilds the table there and is a plain ALTER on PostgreSQL."""
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role_valid", type_="check")
        batch.create_check_constraint("ck_users_role_valid", sa.text(f"role IN {roles}"))


def upgrade() -> None:
    _swap_role_check(NEW_ROLES)


def downgrade() -> None:
    # These rows have to GO, not just be deactivated: the narrower constraint rejects the
    # role itself, whatever is_active says. Deleting them is the honest cost of a downgrade
    # and a cheap one - an app_manager is a read-only mobile login that owns no job, no
    # template and no mailbox, so nothing is orphaned and a Super Admin can re-issue it in
    # seconds. Nothing else is touched.
    op.execute("DELETE FROM users WHERE role = 'app_manager'")
    _swap_role_check(OLD_ROLES)

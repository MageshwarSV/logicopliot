"""connected_mailboxes: a mailbox is now its own thing, independent of any operator user.

Connecting one (see app/api/v1/email_puller_mailboxes.py) needs only its own credentials -
no tenant, no template - and it shows up Unassigned until a Super Admin picks both,
independently, whenever they're ready. Supersedes putting mail_provider/mail_email/
mail_app_password_encrypted/mail_paused directly on an operator's own User row.

This migration also carries every ALREADY-WORKING mailbox forward, so nothing that
currently works stops working the moment this ships:
- tenant_id = that user's own tenant_id (it was already routing there).
- group_id = that user's single UserTemplateAssignment, ONLY when they have EXACTLY one -
  matching email_puller.py's existing "an operator assigned to exactly one template is
  trusted directly" fast path. A user assigned to zero or several templates gets
  group_id = NULL instead (tenant-assigned, template left open) - the puller still
  identifies the customer from the documents across that tenant's templates, same
  multi-candidate behavior as today, just read from this table from now on.

The OLD User.mail_* columns are deliberately left in place, untouched and unused after this -
dropping them is a separate, later migration once the new table has been live a while,
so a mistake here is reversible without another schema change.

Revision ID: q5r3mailbox66
Revises: n4p2aifallback65
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "q5r3mailbox66"
down_revision = "n4p2aifallback65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connected_mailboxes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("app_password_encrypted", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("group_id", sa.String(length=36), sa.ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connected_mailboxes_email", "connected_mailboxes", ["email"])
    op.create_index("ix_connected_mailboxes_tenant_id", "connected_mailboxes", ["tenant_id"])
    op.create_index("ix_connected_mailboxes_group_id", "connected_mailboxes", ["group_id"])

    bind = op.get_bind()
    users = bind.execute(sa.text(
        "SELECT id, tenant_id, mail_provider, mail_email, mail_host, "
        "mail_app_password_encrypted, mail_paused FROM users "
        "WHERE mail_email IS NOT NULL AND mail_app_password_encrypted IS NOT NULL"
    )).fetchall()
    now = sa.func.now()
    for u in users:
        assigned = bind.execute(
            sa.text("SELECT group_id FROM user_template_assignments WHERE user_id = :uid"),
            {"uid": u.id},
        ).fetchall()
        group_id = assigned[0].group_id if len(assigned) == 1 else None
        bind.execute(
            sa.text(
                "INSERT INTO connected_mailboxes "
                "(id, provider, email, host, app_password_encrypted, tenant_id, group_id, "
                " paused, created_at, updated_at) "
                "VALUES (:id, :provider, :email, :host, :pw, :tenant_id, :group_id, :paused, "
                " now(), now()) "
                "ON CONFLICT (email) DO NOTHING"
            ),
            {
                "id": str(uuid.uuid4()),
                "provider": u.mail_provider or "gmail",
                "email": u.mail_email,
                "host": u.mail_host or "",
                "pw": u.mail_app_password_encrypted,
                "tenant_id": u.tenant_id,
                "group_id": group_id,
                "paused": bool(u.mail_paused),
            },
        )


def downgrade() -> None:
    op.drop_index("ix_connected_mailboxes_group_id", table_name="connected_mailboxes")
    op.drop_index("ix_connected_mailboxes_tenant_id", table_name="connected_mailboxes")
    op.drop_index("ix_connected_mailboxes_email", table_name="connected_mailboxes")
    op.drop_table("connected_mailboxes")

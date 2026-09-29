from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

SUPER_ADMIN = "super_admin"
TENANT_ADMIN = "tenant_admin"
OPERATOR = "operator"
# Cross-tenant, like Super Admin, but scoped to jobs only — no tenant/template/user
# management. Created by Super Admin alone, through its own separate flow (never a
# tenant_admin's operator, never bolted onto the tenant-admin creation screen).
ADMIN = "admin"
# Gate Keeper 2 — a tenant-scoped second sign-off role, created by the Tenant Admin (like
# an operator) rather than the Super Admin. Sees only jobs a GK1 (operator) has already
# submitted for approval, restricted to the shipment modes this user was assigned at
# creation (see User.assigned_modes below).
GK2 = "gk2"
# Tenant-scoped, read-only observer over their own tenant's whole operation — every job,
# every GK1/GK2's work, regardless of who it is assigned to or which modes it covers.
# Created by the Tenant Admin, like operator/gk2. Deliberately granted NO write endpoint
# anywhere (approve/edit/upload/submit) — every action a Manager might want is something to
# ask GK1 or GK2 to do, never something Manager does directly.
MANAGER = "manager"
ROLES = (SUPER_ADMIN, TENANT_ADMIN, OPERATOR, ADMIN, GK2, MANAGER)


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {ROLES}", name="ck_users_role_valid"),
        CheckConstraint(
            "(role IN ('super_admin', 'admin') AND tenant_id IS NULL) OR "
            "(role NOT IN ('super_admin', 'admin') AND tenant_id IS NOT NULL)",
            name="ck_users_tenant_scope",
        ),
    )

    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # An operator's own mailbox, polled for documents the same way the tenant's shared inbox
    # is - "gmail" or "zoho". All three are set together or not at all (validated by an IMAP
    # login test before saving) and the password is never stored in the clear.
    mail_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The EXACT host that answered when this was verified - Zoho runs separate regional
    # data centers (imap.zoho.in, imap.zoho.eu, ...), so "zoho" alone is not enough to
    # reconnect; a row saved before this existed simply has none, and the poller falls
    # back to the global default for that provider.
    mail_host: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mail_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    mail_app_password_encrypted: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A per-mailbox on/off switch, independent of the system-wide email_pull_paused flag
    # (app/core/system_settings.py) - lets a Super Admin stop just ONE noisy/misconfigured
    # mailbox from the Settings page without pausing every other operator's inbox too.
    # Meaningless (and never shown) for a user with no mailbox connected at all.
    mail_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Which shipment modes (see app/core/modes.py::MODES) this user is scoped to. The REAL
    # access-control gate for gk2 (null/empty means they see nothing — a mode must be picked
    # at creation, unlike Tenant.allowed_modes where null/empty means unrestricted); for
    # operator it is only a UI tab-filter convenience — their real access control is still
    # UserTemplateAssignment, untouched by this field. Meaningless for every other role
    # (tenant_admin, super_admin, admin, manager — manager sees every job in-tenant
    # unrestricted, on purpose). Mirrors Tenant.allowed_modes.
    assigned_modes: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Set only for a user created under a Tenant Admin's custom type (e.g. "Supervisor").
    # `role` above still carries that type's base_role, so every existing role check
    # (require_role, GK2's mode gate, Manager's read-only lock) works completely unchanged;
    # this only adds the type's own name and its own write_enabled rule on top. Never set for
    # a plain Gate Keeper 1/2 or Manager account created directly, nor for tenant_admin/
    # super_admin/admin — custom types exist only for the three tenant-creatable roles.
    user_type_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("user_types.id", ondelete="RESTRICT"), nullable=True
    )

    tenant: Mapped["Tenant | None"] = relationship(back_populates="users")
    user_type: Mapped["UserType | None"] = relationship()

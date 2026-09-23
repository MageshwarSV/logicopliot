from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# A custom type is always built ON one of these three — it borrows that role's entire
# job-access behavior (operator's own-job template scoping, GK2's approval queue restricted
# to assigned_modes, or Manager's tenant-wide read-only) and only relabels it with the
# tenant's own name plus its own Read/Write rule. A truly independent access pattern would
# need the job-access system rebuilt from scratch; this reuses what already exists.
BASE_ROLES = ("operator", "gk2", "manager")


class UserType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Tenant Admin's own custom user type — e.g. "Supervisor" — layered on top of one of
    the three built-in roles. Users created under it get that role's real access behavior,
    stamped with this type's name and its own write_enabled rule instead of the tenant-wide
    default for that role."""

    __tablename__ = "user_types"
    __table_args__ = (
        CheckConstraint(f"base_role IN {BASE_ROLES}", name="ck_user_types_base_role_valid"),
        UniqueConstraint("tenant_id", "name", name="uq_user_types_tenant_name"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Same meaning as Tenant.role_write_enabled, just scoped to this one custom type instead
    # of every user of that base role. A type based on manager is always locked to False —
    # same permanent lock the Masters page already applies to Manager itself.
    write_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

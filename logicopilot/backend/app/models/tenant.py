from sqlalchemy import Boolean, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Which transport modes (see app/core/modes.py) this client is licensed for — a template
    # can only be tagged with one of these. Empty/null = unrestricted (backward compatible
    # with every tenant that existed before this was added), same rule UserTemplateAssignment
    # already uses: no rows recorded means no restriction.
    allowed_modes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Set on the Tenant Admin's "Masters" page: whether operator/gk2 users in this tenant get
    # the normal read-and-write workflow, or are locked to read-only (same mechanism Manager
    # always has). Shape: {"operator": bool, "gk2": bool} — a missing key or a null column
    # means read-and-write, so every tenant that existed before this was added is unaffected.
    # "manager" never appears here: that role is always read-only, not a per-tenant choice.
    role_write_enabled: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The one key that gets this tenant's IRN Pending link working (see
    # app/api/v1/public_irn.py) - generated once from the Super Admin dashboard's "Generate
    # Link" button and never rotated automatically, so a link already handed out keeps
    # working. Null until generated; unique so one key never resolves to two tenants.
    irn_pending_access_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")

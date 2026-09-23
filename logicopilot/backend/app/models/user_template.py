from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserTemplateAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Which template sets a user (tenant admin / operator) may work with.
    If a user has no assignments, they can see all their tenant's templates
    (backward-compatible); if they have any, they're restricted to those."""

    __tablename__ = "user_template_assignments"
    __table_args__ = (UniqueConstraint("user_id", "group_id"),)

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )

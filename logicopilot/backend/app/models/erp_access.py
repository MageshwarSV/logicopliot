from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ErpAccessRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Tenant Admin's request to wire a template into their ERP — the URL and
    credentials land in the Super Admin's inbox. (Credentials stored as given; for a
    real deployment these should be encrypted at rest.)"""

    __tablename__ = "erp_access_requests"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raised_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open | resolved

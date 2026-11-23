from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ErpBranchSessionUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One user allowed to turn ONE branch login (ErpBranchSession) on and off.

    A Super Admin ticks these per user (Tenants -> Users -> ERP Branch). The user then gets an
    "ERP Branch" button in the top bar listing exactly these logins, so an operator who needs
    to use that ERP user by hand can turn the always-on login off and back on again without a
    Super Admin. No row = no access; deleting the user or the branch login removes the row.
    """

    __tablename__ = "erp_branch_session_users"
    __table_args__ = (UniqueConstraint("user_id", "branch_session_id", name="uq_branch_session_user"),)

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("erp_branch_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ErpBranchSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One always-on ERP login for ONE branch, kept alive by app/core/branch_sessions.py.

    Live Impex keeps a single branch per login, and every tab on that login follows it - so
    jobs for two branches can only run side by side on two different ERP users. Each row is
    one such user: the keeper logs it in with `login_script_id`'s steps up to that script's
    checkpoint, selecting `branch`, and keeps it alive. Every job whose branch value matches
    then opens its own browser on this login and runs its stream script from after the
    checkpoint - many at once, up to `max_tabs`.

    Nobody else may log in as this user: a second login logs the first one out.
    """

    __tablename__ = "erp_branch_sessions"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The branch exactly as the job's branch field carries it (e.g. "Chennai") - matched
    # case-insensitively, and typed into the login script's branch step.
    branch: Mapped[str] = mapped_column(String(100), nullable=False)
    # The script whose steps up to its checkpoint log in and reach the home screen.
    login_script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("erp_scripts.id", ondelete="CASCADE"), nullable=False
    )
    # This branch's own ERP user. Empty = the login script's recorded user ID / password.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which stream scripts (Air Import, Sea Import, ...) may run their jobs on this login.
    # NULL / empty = every branch-routed script of the tenant. Lets one branch have two logins
    # for different scripts (Chennai Air on one user, Chennai Sea on another).
    script_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # How many jobs of this branch may run on the login at once.
    max_tabs: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Written by the keeper: starting | logging_in | live | error | off
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="off", server_default="off")
    status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

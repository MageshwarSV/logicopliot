from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ErpScript(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A recorded ERP web-entry automation (RPA macro), created by the Super Admin.
    Records the target URL + optional login + an ordered list of steps, and which
    template(s) supply the draggable data fields used to fill the ERP form."""

    __tablename__ = "erp_scripts"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)

    has_login: Mapped[bool] = mapped_column(default=False, nullable=False)
    login_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    login_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Template groups whose extracted fields can be dropped into the ERP form.
    template_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Ordered recorded actions. Each step (see schemas): {action, selector, value?,
    # field_label?, options?, description?} — action ∈ navigate|click|fill|select|wait|submit.
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # One recorded step may be marked the CHECKPOINT: the point reached after logging in and
    # navigating to the entry screen. Steps up to it are setup; steps after it are the entry.
    # NULL = no checkpoint = open, run everything, close (unchanged behaviour).
    checkpoint_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Reuse the session reached at the checkpoint for the next job instead of logging in again.
    stay_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | ready
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

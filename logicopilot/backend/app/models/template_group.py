from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class TemplateGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One 'Customer with Template Creation' unit: a named set of documents
    (BL + Invoice + Packing List ...) configured for a tenant in the wizard."""

    __tablename__ = "template_groups"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which transport mode this template is for — one of app/core/modes.py's MODES, e.g.
    # "Sea Import". Asked for at creation and, when the tenant has allowed_modes set,
    # limited to those. Nullable only because templates built before this existed have none.
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft | ready
    # Mail integration: documents sent from this address are auto-pulled for this customer.
    pull_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The operator who owns mail-pulled jobs for this customer (they see them alone).
    pull_operator_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Custom ruling: a natural-language rule that decides, from a job's document data,
    # which documents are actually required (and can ask the operator when unsure).
    ruling_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # HOW the job's data reaches the ERP.
    #   fields — the recorded script types each value into its own box, one at a time
    #   excel  — the ERP takes a bulk import, so the job is written to a workbook and the
    #            script attaches that one file instead of filling dozens of boxes
    entry_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="fields")
    # For entry_mode="excel". Shape:
    #   {"source": "blank" | "template",        # a sheet we create, or the customer's own
    #    "sheet": "Sheet1",                     # which sheet the ERP reads
    #    "header_row": 1,                       # 1-based row holding the column headings
    #    "columns": [{"header": "Container No", "field": "container_number"}, ...],
    #    "file_name": "import.xlsx"}            # what the ERP's file picker will be handed
    # `columns` is ordered: it IS the column order of the produced sheet.
    excel_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    documents: Mapped[list["TemplateDocument"]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="TemplateDocument.order_index"
    )

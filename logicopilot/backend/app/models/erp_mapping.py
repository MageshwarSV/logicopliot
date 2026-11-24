from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ErpWorkbook(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Customer Onboarding, Step 1: a customer's ERP Excel workbook, read sheet by sheet.

    One current workbook per customer template (group_id); uploading a new one replaces it and
    carries over every classification whose sheet + column heading still exists. The uploaded
    file is kept as uploaded (stored_path) and never modified. Separate from the older Excel
    entry upload (TemplateGroup.excel_config), which is left untouched.

    sheets: [{"name", "header_row", "columns": [{"key", "index", "name", "samples",
              "suggested_type"}]}] - straight from the file.
    status: "draft" until every column is classified and the admin finishes the step."""

    __tablename__ = "erp_workbooks"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sheets: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # The customer's documents - tabs in the Document bucket: [{"id", "name",
    # "template_document_id"}]. Seeded from the template's own documents the first time the
    # workbook is opened; the admin can add more (a Bill of Lading not in the template yet).
    documents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ErpField(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One ERP field: an Excel column of the workbook (sheet + column key + heading), or a
    custom field the admin added to a sheet (is_custom, no column). category is None while
    unclassified, else document | reference | fixed | operator | ai | formula - one bucket only. The rest is the
    field's configuration, passed on to the next onboarding steps."""

    __tablename__ = "erp_fields"

    workbook_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("erp_workbooks.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_key: Mapped[str | None] = mapped_column(String(10), nullable=True)     # "A", "AB"; None = custom
    column_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excel_name: Mapped[str] = mapped_column(String(255), nullable=False)          # heading as in the file
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)     # order inside its bucket
    # Which of the customer's documents (ErpWorkbook.documents id) the field belongs to - the
    # document tab it was classified on, whatever its bucket. None = not on a document yet.
    source_document: Mapped[str | None] = mapped_column(String(36), nullable=True)
    display_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)          # expected value
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)              # formula / rule
    ai_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)            # AI-derived: the instruction
    erp_master_ref: Mapped[str | None] = mapped_column(Text, nullable=True)       # erp
    contact_ref: Mapped[str | None] = mapped_column(Text, nullable=True)          # contact
    samples: Mapped[list | None] = mapped_column(JSON, nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ErpMappingHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Audit trail of Step 1: uploads, classifications and configuration changes - who, when,
    and the before/after of what changed."""

    __tablename__ = "erp_mapping_history"

    workbook_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    field_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

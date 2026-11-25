from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ScreenLayout(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How one template document's fields are laid out on the job screen - set in Super Admin
    -> Screen Designer. Placement only: which sections, in which order, which fields in each,
    and the product card's own order and groups. A field's own properties (label, width,
    multi-line, mandatory, dropdown list) stay on the field itself (FieldMark / CustomField),
    so there is one place for each setting.

    Opt-in: a document with no row here is shown exactly as before (the job screen's existing
    code). Deleting the row puts the document straight back on that.

    layout (see app.core.screen_layout for the shape and the checks):
        {"sections": [{"id", "title", "fields": ["m:<mark id>" | "c:<custom field id>", ...]}],
         "product_card": {"fields": ["m:<mark id>", ...],
                          "groups": [{"id", "title", "fields": [...]}]}}
    Fields are referenced by id, never by label, so renaming a label never breaks a layout."""

    __tablename__ = "screen_layouts"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    template_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    layout: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ScreenLayoutHistory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Every save of a ScreenLayout, so an earlier one can be looked at and restored.
    layout is None for "layout removed" (the document went back to the default screen)."""

    __tablename__ = "screen_layout_history"

    template_document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # "save" | "create_from_current" | "remove" | "restore" | "copy"
    action: Mapped[str] = mapped_column(String(30), nullable=False, default="save")
    saved_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    saved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

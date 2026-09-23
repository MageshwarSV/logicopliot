from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CrossDocLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Links the same value across two documents in a group, e.g. bl_number on the
    BL must match bl_number on the Invoice. Created via the Step-3 'present in another
    document?' popup: one link row per chosen target document.

    The SOURCE is either a mark (source_mark_id) or a custom field (source_custom_field_id) -
    exactly one, never both (see ck_cross_doc_links_one_source). A custom field can be the
    source of a cross-check - an AI-computed "Total Amount (Calculated)" verified against the
    Invoice's own "Total" mark - but never the TARGET: target_mark_id stays mark-only, since
    there is nothing to draw a comparison against on a document a custom field has no position
    on. See CustomField.verify_with_other_document for the field-side equivalent of
    FieldMark.verify_with_other_document.
    """

    __tablename__ = "cross_doc_links"
    __table_args__ = (
        CheckConstraint(
            "(source_mark_id IS NOT NULL AND source_custom_field_id IS NULL) OR "
            "(source_mark_id IS NULL AND source_custom_field_id IS NOT NULL)",
            name="ck_cross_doc_links_one_source",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_mark_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("field_marks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source_custom_field_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_mark_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("field_marks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    condition: Mapped[str] = mapped_column(String(30), nullable=False, default="must_equal")

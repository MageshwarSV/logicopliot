from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FieldMark(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A bounding box the admin drew on a document page, with its generated
    extraction profile. 'Mark' == bounding box (the user's term)."""

    __tablename__ = "field_marks"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label_name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "bl_number"
    page_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Normalized 0-1 box coordinates (scale to any image resolution).
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="red")

    # Generated extraction profile (both-mode: caption list + semantic fallback).
    detected_anchor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    example_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchor_variations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    semantic_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_format_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tenant Admin's formatting instruction (e.g. "return the date as MM/DD/YYYY").
    tenant_format_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    verify_with_other_document: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ticked while drawing the field: the operator must confirm/enter this value before the
    # ERP entry may be submitted. Any number of fields on a template can carry it — the job
    # is blocked until every one of them has been answered.
    ask_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Shown to the operator next to the input, so they know what is expected of them —
    # e.g. "T for Transaction, D for Deferred". A tick without this is a blank box.
    ask_operator_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mandatory or optional when ask_operator is on. True = Submit Entry is blocked until the
    # operator supplies it; False = still asked for, but the job can be sent without it (an IGM
    # number that has not arrived yet). Ignored entirely when ask_operator is False.
    ask_operator_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # "This document holds MULTIPLE values for this field" — a line-item table. Extraction
    # returns one value per table row instead of a single value, and the job stores one
    # JobFieldValue per row (see JobFieldValue.row_index).
    is_multi_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # This mark is extracted normally, then its OWN raw value is looked up in a reference
    # table keyed on itself (see CustomFieldReferenceValue.mark_id) - a match replaces the
    # extraction with the table's stored value, no match returns empty rather than the raw
    # extracted text. Distinct from a CustomField of kind="lookup": that keys on a DERIVED,
    # multi-column value from elsewhere on the line; this keys on nothing but the field's own
    # extracted text, and works for a single field (not just per-row custom fields).
    is_target_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # is_target_value only: match on the SAME real-world thing despite OCR/formatting noise
    # ("KUEHNE + NAGEL PVT. LTD." vs "KUEHNE+NAGEL") instead of requiring byte-identical text.
    # Off by default - a near-miss here is fine for a freight forwarder's name, but never for
    # a material code or CTH, which is why this is opt-in per field, not a global behavior.
    fuzzy_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    document: Mapped["TemplateDocument"] = relationship(back_populates="marks")

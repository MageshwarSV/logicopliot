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

    # is_multi_value only: this field's rows are their OWN table, never meant to line up
    # against any other per-row field (a container count has nothing to do with how many
    # products are on the invoice) - unlike item_material_code/product_description, which
    # genuinely are the same line item read off two different documents and must stay
    # aligned. See entry_values_and_rows' own docstring for what this changes.
    standalone_multi_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # standalone_multi_value only: the heading the job screen shows above this field's own
    # group of rows, e.g. "Container" - each tenant's template can word this however fits
    # that tenant's documents, the same way CustomField.picker_heading already lets a picker
    # pair be worded per template rather than with one fixed phrase everywhere. Falls back to
    # the field's own label_name when blank.
    standalone_group_heading: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What the OPERATOR sees on the job screen, when it should read differently from
    # label_name - e.g. label_name stays "HBL No" (what extraction/matching/the Excel export
    # all key on, unchanged), while one tenant's template shows "HAWB Number" instead. None
    # (every field before this existed) falls back to whatever JobRunPage.tsx's own hardcoded
    # FIELD_LABEL_OVERRIDES_BY_DOC_TYPE/BY_GROUP maps already show, unchanged - this is a NEW,
    # higher-priority source on top of those, not a replacement for them.
    display_label: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Admin-settable format check, purely client-side (same enforcement point the earlier
    # hand-coded checks already used - ExtractedField's onBeforeSave, and the document-approval
    # gate's own invalidFormat check - nothing new is enforced server-side). A regex BODY, not
    # anchored - the frontend always tests it as `^(?:pattern)$` against the trimmed value, so
    # an admin typing "[A-Za-z]{3}" (not "^[A-Za-z]{3}$") still gets a whole-value match. None
    # (every field before this existed, and the hand-coded TOI/CustomsHouseCode/Shipment
    # Country Code checks already in JobRunPage.tsx) means no format restriction from this
    # mechanism - those three keep working exactly as they do today until someone moves them
    # over to this column too.
    validation_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Auto-convert to UPPERCASE on save, independent of validation_pattern (a field can have
    # one, both, or neither) - same idea as Shipment Country Code's own existing hand-coded
    # uppercase-on-save.
    validation_uppercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Shown when validation_pattern fails. None = a generic auto-worded message naming the
    # field.
    validation_message: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Spans the full row in the job screen's two-column field grid instead of sharing it with
    # whatever sits beside it - admin-settable per field, replacing the old
    # FULL_WIDTH_FIELD_LABELS hardcoded list in JobRunPage.tsx (kept there too, as a fallback,
    # so a field already relying on it is unaffected). False (half width) is today's existing
    # behavior for every field that predates this column.
    full_width: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Renders as a multi-line, auto-growing box (wraps onto as many lines as the value needs,
    # taller as the value gets longer) instead of a single-line box the operator scrolls
    # sideways through - admin-settable per field, replacing the old MULTILINE_FIELD_LABELS
    # hardcoded list in JobRunPage.tsx (kept there too, as a fallback). False (single line) is
    # today's existing behavior for every field that predates this column.
    multiline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Where this field sits, lowest-first, among the other fields under the SAME section
    # heading on the job screen - admin-settable, so reordering never needs a deploy. None
    # (every field that predates this column) falls back to whatever position the old hardcoded
    # FIELD_GROUPS_BY_DOC_TYPE label-order list in JobRunPage.tsx already gives it (unchanged),
    # or last, for a field that list never mentioned. Only meaningful relative to sibling
    # fields sharing the same heading - not a global ordering.
    field_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    document: Mapped["TemplateDocument"] = relationship(back_populates="marks")

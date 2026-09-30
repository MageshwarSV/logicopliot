from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


# The kinds a custom field can be. Kept here, next to the column, because a whitelist that
# lived only in the edit route fell out of step the moment "lookup" was added: the route dropped
# the value, answered 200, and the screen quietly did nothing.
CUSTOM_FIELD_KINDS = ("hardcoded", "ai", "lookup", "composite")


class CustomField(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A non-cropped field on a template set: either a fixed hardcoded value, or a value
    the AI computes/extracts from one or more of the set's documents (e.g. a calculation
    across the invoice + packing list). Defined by the Super Admin in the wizard."""

    __tablename__ = "custom_fields"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # hardcoded | ai | lookup
    #   lookup - the value comes from the customer's own material master, keyed on another
    #   field of the same line. Used for the CTH/HS code: the classification of a part is
    #   settled long before any shipment, the documents do not carry it, and asking an operator
    #   to type it on every line of every job is the thing this exists to avoid.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="ai")
    hardcoded_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which of the set's documents feed the AI (template_document ids). Empty = all documents.
    source_document_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # kind="ai" only: instead of one value for the whole field, read its own document(s)
    # directly and write one JobFieldValue per row found there (every container number, every
    # line with no mark of its own) — the AI-computed counterpart to a Mark ticked "multiple
    # values in this document". Ignored for "hardcoded"/"lookup".
    multi_value_from_document: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The operator must confirm this value before the ERP entry may be submitted. Used for
    # values that appear on no document at all — e.g. the insurance percentage, where the
    # hardcoded value is a default the operator checks rather than a constant.
    ask_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Guidance shown to the operator at Submit Entry, e.g. "T for Transaction, D for Deferred".
    ask_operator_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Mandatory or optional when ask_operator is on. True = Submit Entry is blocked until the
    # operator supplies it; False = still asked for, but the job can be sent without it (an IGM
    # number that has not arrived yet). Ignored entirely when ask_operator is False.
    ask_operator_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # One value per LINE ITEM instead of one per job. Used with ask_operator for a value the
    # documents cannot supply and that differs per product - the CTH / HS code. The job gets
    # one empty slot per invoice line, numbered, and the synchronised row loop feeds line N's
    # value into line N of the ERP product grid.
    per_row: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # kind="lookup" only: which field of the SAME LINE supplies the key to look up. For Nokia
    # that is the material code read off the invoice. Empty falls back to the line's material
    # code and then to the leading token of its description, the way the invoices are written.
    lookup_key_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Which columns of the reference sheet to match that key against, and which column to bring
    # back. Left empty, the sheet is read the way a material master usually is - match on
    # anything called "material"/"part" or a description, return anything called
    # "comm"/"code"/"cth". Naming them explicitly is what lets the same sheet answer a second
    # question: match the part, return the duty rate instead of the classification.
    lookup_match_columns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lookup_return_column: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Mirrors FieldMark.verify_with_other_document: this field is meant to be cross-checked
    # against a mark on another document (e.g. "Total Amount (Calculated)" vs the Invoice's own
    # "Total"). The actual pairing(s) live as CrossDocLink rows with source_custom_field_id set
    # to this field's id; this flag is the tick itself, kept true even before a target mark has
    # been picked.
    verify_with_other_document: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Mirrors FieldMark.is_target_value: this field's own computed/extracted value is looked
    # up in a reference table keyed on itself (CustomFieldReferenceValue.custom_field_id) - a
    # match replaces it with the table's stored value, no match returns empty. Independent of
    # kind="lookup" above, which keys on a DERIVED value from elsewhere on the line rather than
    # the field's own value, and only ever applies to per_row fields.
    is_target_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # is_target_value only: match on the SAME real-world thing despite OCR/formatting noise
    # ("KUEHNE + NAGEL PVT. LTD." vs "KUEHNE+NAGEL") instead of requiring byte-identical text.
    # Off by default - a near-miss here is fine for a freight forwarder's name, but never for
    # a material code or CTH, which is why this is opt-in per field, not a global behavior.
    fuzzy_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Mirrors FieldMark.example_value: typed live into the ERP while a Super Admin is
    # RECORDING a script (see _sample_for_label), so the field validates and the steps after
    # it can be recorded - never used as the field's real answer on any actual job. Mainly for
    # an ask_operator field with no hardcoded_value of its own (a "Manual Entry" field created
    # straight from the recorder, which otherwise has nothing to type but its own label - not
    # a valid quotation number, PO number, etc. on the live ERP).
    example_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Links this field to ANOTHER already-fully-configured custom field (of any kind) into a
    # picker pair - e.g. a reference-sheet "Dump CTH Number" paired with a document-read
    # "Document CTH". Deliberately not a new kind: each half keeps computing its own value
    # exactly as its own kind already does (hardcoded/ai/lookup, unchanged); pairing only adds
    # a UI picker on top and a write-through onto THIS field's own corrected value, which is
    # the one export actually reads. Set on the field the operator's pick writes INTO - the
    # other half is read from, never written to, by the picker itself.
    paired_custom_field_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("custom_fields.id", ondelete="SET NULL"), nullable=True
    )
    # The heading shown above the two options, e.g. "CTH - pick which one is right for this
    # line". Blank falls back to a generic "Pick which one is right for this line".
    picker_heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Other custom_field ids (usually just one, e.g. "RITC No.") that also get set to whichever
    # value the operator picks, on the same row - fields that are supposed to carry the same
    # answer as this one but are tracked separately (their own reference-sheet lookup, their
    # own export column) rather than silently falling out of sync with it.
    sync_field_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # kind="composite" only: an ORDERED list of other label_names (marks or custom fields,
    # whatever this line already has a value for) whose values get read for the SAME line and
    # joined with a single space, skipping any piece that is blank for that line. No AI prompt
    # and no reference sheet - purely a join over data already extracted/computed elsewhere.
    # Order matters (this is a list, unlike sync_field_ids above, which is an unordered set) -
    # it is exactly the sequence the pieces are concatenated in.
    composite_source_labels: Mapped[list | None] = mapped_column(JSON, nullable=True)


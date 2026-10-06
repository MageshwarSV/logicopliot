from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Step 1 — declare the group and its documents
# --------------------------------------------------------------------------- #
class DocumentDeclaration(BaseModel):
    name: str
    doc_type: str = "Custom"
    # False = the job can move past Document Capture without this one ever being uploaded.
    # True (the default) is the behaviour every template already had before this existed.
    is_required: bool = True


class TemplateGroupCreate(BaseModel):
    tenant_id: str
    name: str
    # Which transport mode this template is for — see app/core/modes.py. Required: every
    # template built from here on has to say what it's for, not just what documents it reads.
    mode: str
    documents: list[DocumentDeclaration] = Field(min_length=1)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    name: str
    doc_type: str
    order_index: int
    page_count: int
    is_uploaded: bool
    # False = Document Capture can complete without this one ever being uploaded.
    is_required: bool = True
    # NOTE: file_path is intentionally NOT exposed.


class MarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    label_name: str
    page_number: int
    x: float
    y: float
    width: float
    height: float
    color: str
    detected_anchor: str | None
    example_value: str | None
    anchor_variations: list[str] | None
    semantic_description: str | None
    value_format_hint: str | None
    extraction_prompt: str | None
    correction_prompt: str | None
    tenant_format_prompt: str | None = None
    verify_with_other_document: bool
    # Operator must confirm this value before the ERP entry can be submitted.
    ask_operator: bool = False
    ask_operator_required: bool = True
    ask_operator_hint: str | None = None
    is_multi_value: bool = False
    standalone_multi_value: bool = False
    standalone_group_heading: str | None = None
    is_target_value: bool = False
    fuzzy_match: bool = False


class CrossDocLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    source_mark_id: str | None = None
    source_custom_field_id: str | None = None
    target_mark_id: str
    condition: str


class CustomFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    label_name: str
    kind: str  # hardcoded | ai | lookup
    hardcoded_value: str | None
    ai_prompt: str | None
    source_document_ids: list[str] | None
    ask_operator: bool = False
    ask_operator_required: bool = True
    per_row: bool = False
    multi_value_from_document: bool = False
    ask_operator_hint: str | None = None
    # kind="lookup": the line field whose value is the key into the reference sheet, and which
    # of the sheet's columns to match against / bring back.
    lookup_key_label: str | None = None
    lookup_match_columns: list[str] | None = None
    lookup_return_column: str | None = None
    # See CrossDocLink.source_custom_field_id - this field is linked to a mark on another
    # document for cross-verification. The tick itself; the actual pairing(s) are separate rows.
    verify_with_other_document: bool = False
    # This field's own computed value is looked up in a reference table keyed on itself - a
    # match replaces it, no match returns empty. See CustomFieldReferenceValue.custom_field_id.
    is_target_value: bool = False
    fuzzy_match: bool = False
    # Typed live into the ERP while recording a script - see FieldMark.example_value.
    example_value: str | None = None
    # Links this field to another already-configured custom field into a picker pair - see
    # CustomField.paired_custom_field_id's own docstring.
    paired_custom_field_id: str | None = None
    picker_heading: str | None = None
    sync_field_ids: list[str] | None = None
    # kind="composite": ordered label_names of other fields on the same line, joined with a
    # single space (blank pieces skipped) - see CustomField.composite_source_labels.
    # Each piece is either another field's label_name (a string) or a fixed literal value
    # typed straight in ({"fixed": "<text>"}) - see app.api.v1.jobs._composite_piece_value.
    composite_source_labels: list[str | dict[str, str]] | None = None


class CustomFieldCreate(BaseModel):
    label_name: str
    kind: str = "ai"  # hardcoded | ai | lookup
    hardcoded_value: str | None = None
    ai_prompt: str | None = None
    source_document_ids: list[str] = []  # empty = use all documents
    ask_operator: bool = False
    ask_operator_hint: str | None = None
    ask_operator_required: bool = True  # False = optional, never blocks Submit Entry
    per_row: bool = False  # one value per line item instead of one per job
    # kind="ai" only: read this field's own value directly off its document(s) as a LIST
    # instead of a single value — one JobFieldValue per row found.
    multi_value_from_document: bool = False
    # kind="lookup": which line field supplies the key. Empty = the line's material code, then
    # the leading token of its description.
    lookup_key_label: str | None = None
    lookup_match_columns: list[str] | None = None
    lookup_return_column: str | None = None
    is_target_value: bool = False
    fuzzy_match: bool = False
    example_value: str | None = None
    paired_custom_field_id: str | None = None
    picker_heading: str | None = None
    sync_field_ids: list[str] | None = None
    # Each piece is either another field's label_name (a string) or a fixed literal value
    # typed straight in ({"fixed": "<text>"}) - see app.api.v1.jobs._composite_piece_value.
    composite_source_labels: list[str | dict[str, str]] | None = None


class CustomFieldEdit(BaseModel):
    """Edit an existing custom tag in place.

    Every field is optional: only what is sent is changed. Editing rather than
    delete-and-recreate matters because job_field_values.custom_field_id is ON DELETE
    CASCADE — removing a tag would wipe that field's value from every past job.
    """

    label_name: str | None = None
    kind: str | None = None  # hardcoded | ai | lookup
    hardcoded_value: str | None = None
    ai_prompt: str | None = None
    source_document_ids: list[str] | None = None
    lookup_key_label: str | None = None
    lookup_match_columns: list[str] | None = None
    lookup_return_column: str | None = None
    ask_operator: bool | None = None
    ask_operator_required: bool | None = None
    per_row: bool | None = None
    multi_value_from_document: bool | None = None
    ask_operator_hint: str | None = None
    is_target_value: bool | None = None
    fuzzy_match: bool | None = None
    verify_with_other_document: bool | None = None
    example_value: str | None = None
    paired_custom_field_id: str | None = None
    picker_heading: str | None = None
    sync_field_ids: list[str] | None = None
    # Each piece is either another field's label_name (a string) or a fixed literal value
    # typed straight in ({"fixed": "<text>"}) - see app.api.v1.jobs._composite_piece_value.
    composite_source_labels: list[str | dict[str, str]] | None = None


class DocumentDetailOut(DocumentOut):
    marks: list[MarkOut] = []


class TemplateGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    status: str
    mode: str | None = None
    pull_email: str | None = None
    pull_operator_id: str | None = None
    ruling_prompt: str | None = None
    # How this customer's data reaches the ERP: typed field by field, or handed over as one
    # workbook for a bulk import.
    entry_mode: str = "fields"
    excel_config: dict | None = None


class TemplateGroupDetailOut(TemplateGroupOut):
    documents: list[DocumentDetailOut] = []
    cross_doc_links: list[CrossDocLinkOut] = []
    custom_fields: list[CustomFieldOut] = []


# --------------------------------------------------------------------------- #
# Step 3 — create a mark (bounding box)
# --------------------------------------------------------------------------- #
class MarkCreate(BaseModel):
    label_name: str
    page_number: int = 1
    x: float
    y: float
    width: float
    height: float
    color: str = "red"
    verify_with_other_document: bool = False
    ask_operator: bool = False
    ask_operator_hint: str | None = None
    ask_operator_required: bool = True  # False = optional, never blocks Submit Entry
    is_multi_value: bool = False
    standalone_multi_value: bool = False
    standalone_group_heading: str | None = None
    is_target_value: bool = False
    fuzzy_match: bool = False


class MarkCorrect(BaseModel):
    correction_prompt: str


class MarkEdit(BaseModel):
    """Direct edits to a field's prompt/anchors (Super Admin, Prompts step)."""

    label_name: str | None = None
    extraction_prompt: str | None = None
    anchor_variations: list[str] | None = None
    semantic_description: str | None = None
    value_format_hint: str | None = None
    ask_operator: bool | None = None
    ask_operator_hint: str | None = None
    ask_operator_required: bool | None = None
    is_multi_value: bool | None = None
    standalone_multi_value: bool | None = None
    standalone_group_heading: str | None = None
    is_target_value: bool | None = None
    fuzzy_match: bool | None = None
    # The two pieces of evidence the wizard captures AUTOMATICALLY when a box is drawn - the
    # text it found around the box, and the value inside it. On a two-column layout the words
    # beside a value are not its caption, so both can come out wrong: a Gross Weight box that
    # recorded "Net Weight : 40.64 Kgs" as its anchor, a Rate box that recorded a product
    # description. They are fed to the reader as "the text around this box read ...", so a
    # wrong one actively misleads it - and until now nothing could correct them.
    detected_anchor: str | None = None
    example_value: str | None = None


# --------------------------------------------------------------------------- #
# Step 3 cross-doc — link a source mark to a target mark
# --------------------------------------------------------------------------- #
class CrossDocLinkCreate(BaseModel):
    source_mark_id: str
    target_mark_id: str
    condition: str = "must_equal"


class LinkFieldToDocuments(BaseModel):
    """Link a source field to other documents WITHOUT re-cropping: the field's
    semantic profile is copied so extraction finds the same value on each target doc."""

    target_document_ids: list[str]
    condition: str = "must_equal"


class LinkCustomFieldToMarks(BaseModel):
    """Link a custom field (an AI-computed value with no position on any document) to
    EXISTING marks on other documents. Unlike LinkFieldToDocuments (a mark, matched onto
    other documents by its own label), a custom field's label rarely matches any mark's -
    "Total Amount (Calculated)" has no mark called that anywhere - so the admin picks the
    mark(s) directly rather than relying on a name match."""

    target_mark_ids: list[str]
    condition: str = "must_equal"


# --------------------------------------------------------------------------- #
# Step 5 — demo run (what each mark extracts from the reference doc)
# --------------------------------------------------------------------------- #
class DemoFieldResult(BaseModel):
    mark_id: str
    label_name: str
    extracted_value: str | None
    matched_anchor: str | None
    # Populated for multi-value fields: every row found, in document order.
    extracted_values: list[str | None] | None = None


class DemoResult(BaseModel):
    document_id: str
    results: list[DemoFieldResult]
    # Only populated when test_extract's own debug=true is passed (structure-engine
    # verification only - see field_marks.py) - the exact ocr_text the fields above were
    # read from, so the admin API can inspect it directly instead of guessing why a value
    # came out wrong.
    debug_text: str | None = None
    # Same debug=true gate: the raw paragraph/table bounding boxes (x0/y0/x1/y1/text/kind)
    # docai.py's structure engine actually saw, one list per page - lets a real page's
    # geometry be inspected directly rather than inferred from its text alone.
    debug_blocks: list[list[dict]] | None = None

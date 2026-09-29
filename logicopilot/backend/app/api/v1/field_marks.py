import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.anchors import detect_value_and_anchor
from app.core.config import get_settings
from app.core.deps import get_db, require_role
from app.core.docai import get_page_ocr, ocr_page_image
from app.core.extraction import (
    extract_document_fields,
    extract_document_fields_from_images,
    extract_document_rows,
    extract_document_rows_from_images,
    field_spec,
)
from app.core.llm import build_field_profile
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User
from app.schemas.onboarding import (
    CustomFieldEdit,
    CrossDocLinkCreate,
    CrossDocLinkOut,
    CustomFieldCreate,
    DemoFieldResult,
    DemoResult,
    LinkCustomFieldToMarks,
    LinkFieldToDocuments,
    MarkCorrect,
    MarkEdit,
    MarkCreate,
    MarkOut,
)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_TEST_BYTES = 25 * 1024 * 1024

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding: marks"])


def _document_dir(document_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "documents" / document_id


def _box(mark_or_payload) -> dict:
    return {
        "x0": mark_or_payload.x,
        "y0": mark_or_payload.y,
        "x1": mark_or_payload.x + mark_or_payload.width,
        "y1": mark_or_payload.y + mark_or_payload.height,
    }


def _apply_profile(mark: FieldMark, document: TemplateDocument, db: Session) -> None:
    """Best-effort: OCR the page (cached), detect value + anchor, build the extraction
    profile. A Document AI outage leaves the mark saved with a null profile for later
    re-detection rather than failing the whole request."""
    value_text = anchor = None
    try:
        ocr = get_page_ocr(_document_dir(document.id), mark.page_number)
        value_text, anchor = detect_value_and_anchor(ocr, _box(mark))
    except Exception:  # noqa: BLE001
        logger.exception("OCR/anchor detection failed for mark %s", mark.id)

    # For "Custom" docs the type carries no meaning, so feed the human document name
    # (e.g. "Insurance Certificate") as context; otherwise combine name + type.
    doc_context = document.name if document.doc_type == "Custom" else f"{document.name} ({document.doc_type})"
    profile = build_field_profile(
        field_name=mark.label_name,
        anchor_term=anchor,
        value_text=value_text,
        document_type=doc_context,
        correction_prompt=mark.correction_prompt,
    )
    mark.detected_anchor = anchor
    mark.example_value = value_text
    mark.anchor_variations = profile.anchor_variations
    mark.semantic_description = profile.semantic_description
    mark.value_format_hint = profile.value_format_hint
    mark.extraction_prompt = profile.extraction_prompt
    db.commit()
    db.refresh(mark)


@router.post(
    "/template-documents/{document_id}/marks",
    response_model=MarkOut,
    status_code=status.HTTP_201_CREATED,
)
def create_mark(
    document_id: str,
    payload: MarkCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> MarkOut:
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if not document.is_uploaded:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload the document file first")
    for name in ("x", "y", "width", "height"):
        if not 0 <= getattr(payload, name) <= 1:
            raise HTTPException(status_code=422, detail=f"{name} must be a normalized 0-1 coordinate")
    if payload.width <= 0.001 or payload.height <= 0.001:
        raise HTTPException(status_code=422, detail="Mark box is too small")

    mark = FieldMark(
        tenant_id=document.tenant_id,
        document_id=document.id,
        label_name=payload.label_name,
        page_number=payload.page_number,
        x=payload.x,
        y=payload.y,
        width=payload.width,
        height=payload.height,
        color=payload.color,
        verify_with_other_document=payload.verify_with_other_document,
        ask_operator=payload.ask_operator,
        ask_operator_required=payload.ask_operator_required,
        ask_operator_hint=payload.ask_operator_hint,
        is_multi_value=payload.is_multi_value,
        is_target_value=payload.is_target_value,
        fuzzy_match=payload.fuzzy_match,
    )
    db.add(mark)
    db.commit()
    db.refresh(mark)

    _apply_profile(mark, document, db)  # Step 4: saved as a system prompt
    return MarkOut.model_validate(mark)


@router.get("/template-documents/{document_id}/marks", response_model=list[MarkOut])
def list_marks(
    document_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[MarkOut]:
    if db.get(TemplateDocument, document_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    marks = db.query(FieldMark).filter(FieldMark.document_id == document_id).all()
    return [MarkOut.model_validate(m) for m in marks]


@router.delete("/marks/{mark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mark(
    mark_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    """Delete a field, and any cross-doc "verify with another document" copies that exist
    only to point at it.

    link_field_to_documents() makes those copies with a zero-area box - "this field was never
    cropped on THIS document, it is found by meaning" - so once nothing links to one any more
    it has no life of its own. The link row itself cascades away with either mark (the FK is
    ondelete=CASCADE), but the copy FIELD did not: deleting the field the operator actually
    drew left its invisible duplicates behind on every other linked document, still showing up
    everywhere a group's marks are listed - Data Transformation included - as if they were
    real fields nobody had named.
    """
    mark = db.get(FieldMark, mark_id)
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")

    own_links = db.query(CrossDocLink).filter(
        (CrossDocLink.source_mark_id == mark.id) | (CrossDocLink.target_mark_id == mark.id)
    ).all()
    # The other side of each link - another mark, UNLESS this mark is the TARGET of a
    # custom-field-sourced link (see CrossDocLink.source_custom_field_id), in which case
    # there is no other mark at all, only the custom field whose own tick may now be stale.
    other_mark_ids = {
        (link.target_mark_id if link.source_mark_id == mark.id else link.source_mark_id)
        for link in own_links if link.source_mark_id is not None
    }
    custom_field_ids = {link.source_custom_field_id for link in own_links
                        if link.source_mark_id is None and link.target_mark_id == mark.id}
    # Deleted explicitly rather than left to the FK's ON DELETE CASCADE: correct everywhere
    # that way, whereas relying on it silently does nothing on a database (or test setup)
    # that never turned foreign key enforcement on in the first place.
    for link in own_links:
        db.delete(link)
    db.delete(mark)
    db.flush()

    for other_id in other_mark_ids:
        other = db.get(FieldMark, other_id)
        if other is None:
            continue
        still_linked = db.query(CrossDocLink).filter(
            (CrossDocLink.source_mark_id == other.id) | (CrossDocLink.target_mark_id == other.id)
        ).first()
        # A real, hand-drawn field (non-zero area) is never swept up here even if this was
        # its last link - only the meaning-only copies this endpoint itself creates.
        if other.width == 0 and other.height == 0 and still_linked is None:
            db.delete(other)

    if custom_field_ids:
        from app.models.custom_field import CustomField

        for cf in db.query(CustomField).filter(CustomField.id.in_(custom_field_ids)).all():
            still_linked = db.query(CrossDocLink).filter(
                CrossDocLink.source_custom_field_id == cf.id).first()
            if still_linked is None:
                cf.verify_with_other_document = False

    db.commit()


def _rename_field_in_group_excel_config(db: Session, document_id: str, old_label: str, new_label: str) -> None:
    """A mark's rename needs one extra hop (document -> group) that a custom field's own
    group_id already has directly - see edit_custom_field's own inline call. See
    rename_field_in_excel_config's own docstring for why this matters."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.excel_entry import rename_field_in_excel_config
    from app.models.template_group import TemplateGroup

    document = db.get(TemplateDocument, document_id)
    if document is None:
        return
    group = db.get(TemplateGroup, document.group_id)
    if group is None or not group.excel_config:
        return
    if rename_field_in_excel_config(group.excel_config, old_label, new_label):
        flag_modified(group, "excel_config")


@router.patch("/marks/{mark_id}", response_model=MarkOut)
def edit_mark(
    mark_id: str,
    payload: MarkEdit,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> MarkOut:
    """Directly edit a field's prompt / anchor variations / label (Prompts step).
    Unlike /correct, this saves exactly what the admin typed (no AI regeneration)."""
    mark = db.get(FieldMark, mark_id)
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")
    old_label = mark.label_name
    updates = payload.model_dump(exclude_unset=True)
    if "anchor_variations" in updates and updates["anchor_variations"] is not None:
        # de-dupe, drop blanks, keep order
        seen: set[str] = set()
        cleaned: list[str] = []
        for v in updates["anchor_variations"]:
            v = (v or "").strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                cleaned.append(v)
        updates["anchor_variations"] = cleaned
    for field, value in updates.items():
        setattr(mark, field, value)
    new_label = updates.get("label_name")
    if new_label and new_label != old_label:
        _rename_field_in_group_excel_config(db, mark.document_id, old_label, new_label)
    db.commit()
    db.refresh(mark)
    return MarkOut.model_validate(mark)


@router.patch("/marks/{mark_id}/correct", response_model=MarkOut)
def correct_mark(
    mark_id: str,
    payload: MarkCorrect,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> MarkOut:
    """Step 6: fold a human correction into the field and regenerate its prompt."""
    mark = db.get(FieldMark, mark_id)
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")
    document = db.get(TemplateDocument, mark.document_id)
    mark.correction_prompt = payload.correction_prompt
    db.commit()
    _apply_profile(mark, document, db)
    return MarkOut.model_validate(mark)


@router.get("/template-documents/{document_id}/demo", response_model=DemoResult)
def demo_extract(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> DemoResult:
    """Step 5: show what each mark extracts from the reference document.

    Tenant Admins need this too — it is how the Data Transformation review page lists the
    individual rows of a multi-value field, which a single example_value cannot represent.
    They are restricted to their own tenant's documents.
    """
    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if user.role == TENANT_ADMIN and document.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    marks = db.query(FieldMark).filter(FieldMark.document_id == document_id).all()

    results: list[DemoFieldResult] = []
    ocr_by_page: dict[int, dict] = {}

    # Multi-value fields span a whole table, so geometry cannot answer them: OCR-ing the box
    # yields one unreadable blob of every cell. Read the table once, row by row, and return
    # each row so the caller can list them as "<label> 1 .. <label> N".
    multi = [m for m in marks if m.is_multi_value]
    multi_rows: list[dict] = []
    if multi:
        try:
            from app.core.extraction import extract_document_rows

            pages: list[str] = []
            for page in range(1, (document.page_count or 1) + 1):
                try:
                    page_ocr = get_page_ocr(_document_dir(document_id), page)
                    pages.append(page_ocr.get("layout_text") or page_ocr.get("text", ""))
                except Exception:  # noqa: BLE001
                    pass
            multi_rows = extract_document_rows(
                "\n".join(pages),
                # The same spec a real job builds. This used to be its own shorter copy
                # without the anchor or the example, so the wizard's demo measured something
                # the live system does not do.
                [field_spec(m, inline_format=False) for m in multi],
            )
        except Exception:  # noqa: BLE001 — a failure here must not break the whole demo
            logger.exception("Demo row extraction failed for document %s", document_id)

    for mark in marks:
        if mark.is_multi_value:
            vals = [r.get(mark.label_name) for r in multi_rows]
            preview = None
            if vals:
                shown = [str(v) for v in vals[:3] if v]
                preview = f"{len(vals)} value(s): " + ", ".join(shown) + (" …" if len(vals) > 3 else "")
            results.append(
                DemoFieldResult(
                    mark_id=mark.id,
                    label_name=mark.label_name,
                    extracted_value=preview,
                    matched_anchor=mark.detected_anchor,
                    extracted_values=vals,
                )
            )
            continue

        value = mark.example_value
        try:
            if mark.page_number not in ocr_by_page:
                ocr_by_page[mark.page_number] = get_page_ocr(_document_dir(document_id), mark.page_number)
            value, _ = detect_value_and_anchor(ocr_by_page[mark.page_number], _box(mark))
        except Exception:  # noqa: BLE001 — fall back to the stored example value
            logger.exception("Demo extraction failed for mark %s", mark.id)
        results.append(
            DemoFieldResult(
                mark_id=mark.id,
                label_name=mark.label_name,
                extracted_value=value,
                matched_anchor=mark.detected_anchor,
            )
        )
    return DemoResult(document_id=document_id, results=results)


# --------------------------------------------------------------------------- #
# Cross-document links (Step 3 "present in another document?" popup)
# --------------------------------------------------------------------------- #
@router.post("/cross-doc-links", response_model=CrossDocLinkOut, status_code=status.HTTP_201_CREATED)
def create_cross_doc_link(
    payload: CrossDocLinkCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> CrossDocLinkOut:
    source = db.get(FieldMark, payload.source_mark_id)
    target = db.get(FieldMark, payload.target_mark_id)
    if source is None or target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")
    source_doc = db.get(TemplateDocument, source.document_id)
    target_doc = db.get(TemplateDocument, target.document_id)
    if source_doc.group_id != target_doc.group_id:
        raise HTTPException(status_code=422, detail="Both marks must belong to the same template group")
    if source.document_id == target.document_id:
        raise HTTPException(status_code=422, detail="Link must connect marks on two different documents")

    link = CrossDocLink(
        tenant_id=source.tenant_id,
        group_id=source_doc.group_id,
        source_mark_id=source.id,
        target_mark_id=target.id,
        condition=payload.condition,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return CrossDocLinkOut.model_validate(link)


@router.post(
    "/field-marks/{source_mark_id}/link-documents",
    response_model=list[CrossDocLinkOut],
    status_code=status.HTTP_201_CREATED,
)
def link_field_to_documents(
    source_mark_id: str,
    payload: LinkFieldToDocuments,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[CrossDocLinkOut]:
    """Cross-doc verify WITHOUT re-cropping: for each target document, copy the source
    field's semantic profile onto a field there (so extraction finds the same value by
    meaning) and link them. No second bounding box needed."""
    source = db.get(FieldMark, source_mark_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source field not found")
    source_doc = db.get(TemplateDocument, source.document_id)

    source.verify_with_other_document = True
    created: list[CrossDocLink] = []
    for doc_id in dict.fromkeys(payload.target_document_ids):
        if doc_id == source.document_id:
            continue
        tdoc = db.get(TemplateDocument, doc_id)
        if tdoc is None or tdoc.group_id != source_doc.group_id:
            raise HTTPException(status_code=422, detail="Target document is not in the same template group.")

        # Reuse an existing field with this label on the target doc, else create one that
        # carries the SOURCE field's profile (no coordinates needed — extraction is prompt/semantic).
        target = (
            db.query(FieldMark)
            .filter(FieldMark.document_id == doc_id, FieldMark.label_name == source.label_name)
            .first()
        )
        if target is None:
            target = FieldMark(
                tenant_id=source.tenant_id,
                document_id=doc_id,
                label_name=source.label_name,
                page_number=1,
                # Zero-area box: this field was never cropped on THIS document, it is found
                # by meaning. Copying the source's coordinates would paint a box over an
                # unrelated region of the target page; a zero area tells the UI not to draw
                # one at all. Extraction ignores coordinates either way.
                x=0.0, y=0.0, width=0.0, height=0.0,
                color=source.color,
                detected_anchor=source.detected_anchor,
                anchor_variations=source.anchor_variations,
                semantic_description=source.semantic_description,
                value_format_hint=source.value_format_hint,
                extraction_prompt=source.extraction_prompt,
                verify_with_other_document=True,
                # A line-item field must stay a line-item field on the target document. Without
                # this the copy is read as ONE value while the source yields many, and the
                # cross-check has nothing comparable to work with.
                is_multi_value=source.is_multi_value,
            )
            db.add(target)
            db.flush()

        # Skip if this exact link already exists.
        exists = (
            db.query(CrossDocLink)
            .filter(CrossDocLink.source_mark_id == source.id, CrossDocLink.target_mark_id == target.id)
            .first()
        )
        if exists:
            continue
        link = CrossDocLink(
            tenant_id=source.tenant_id,
            group_id=source_doc.group_id,
            source_mark_id=source.id,
            target_mark_id=target.id,
            condition=payload.condition,
        )
        db.add(link)
        db.flush()
        created.append(link)

    db.commit()
    return [CrossDocLinkOut.model_validate(c) for c in created]


@router.post(
    "/custom-fields/{custom_field_id}/link-marks",
    response_model=list[CrossDocLinkOut],
    status_code=status.HTTP_201_CREATED,
)
def link_custom_field_to_marks(
    custom_field_id: str,
    payload: LinkCustomFieldToMarks,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> list[CrossDocLinkOut]:
    """Cross-verify a custom field (an AI-computed value with no position on any document)
    against mark(s) picked directly on other documents - "Total Amount (Calculated)" against
    the Invoice's own "Total" mark, say. Unlike link_field_to_documents, nothing is copied or
    auto-created: a custom field's label rarely matches any mark's, so the admin names the
    target mark(s) themselves rather than picking documents and hoping a same-named field
    turns up on each one.
    """
    from app.models.custom_field import CustomField

    source = db.get(CustomField, custom_field_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found")

    source.verify_with_other_document = True
    created: list[CrossDocLink] = []
    for mark_id in dict.fromkeys(payload.target_mark_ids):
        target = db.get(FieldMark, mark_id)
        if target is None:
            raise HTTPException(status_code=422, detail=f"Target mark {mark_id!r} not found.")
        target_doc = db.get(TemplateDocument, target.document_id)
        if target_doc is None or target_doc.group_id != source.group_id:
            raise HTTPException(
                status_code=422, detail="Target mark is not in the same template group.")

        exists = (
            db.query(CrossDocLink)
            .filter(CrossDocLink.source_custom_field_id == source.id,
                    CrossDocLink.target_mark_id == target.id)
            .first()
        )
        if exists:
            continue
        link = CrossDocLink(
            tenant_id=source.tenant_id,
            group_id=source.group_id,
            source_custom_field_id=source.id,
            target_mark_id=target.id,
            condition=payload.condition,
        )
        db.add(link)
        db.flush()
        created.append(link)

    db.commit()
    return [CrossDocLinkOut.model_validate(c) for c in created]


@router.delete(
    "/custom-fields/{custom_field_id}/link-marks/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_custom_field_from_mark(
    custom_field_id: str,
    link_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    """Remove one cross-verification pairing. If none are left for this custom field, the
    tick itself (verify_with_other_document) is cleared too - it means "linked to something",
    and once nothing is linked that is no longer true."""
    from app.models.custom_field import CustomField

    link = db.get(CrossDocLink, link_id)
    if link is None or link.source_custom_field_id != custom_field_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    db.delete(link)
    db.flush()
    remaining = db.query(CrossDocLink).filter(
        CrossDocLink.source_custom_field_id == custom_field_id).count()
    if remaining == 0:
        source = db.get(CustomField, custom_field_id)
        if source is not None:
            source.verify_with_other_document = False
    db.commit()


@router.post("/template-groups/{group_id}/custom-fields", status_code=status.HTTP_201_CREATED)
def create_custom_field(
    group_id: str,
    payload: CustomFieldCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """Add a custom field to a template set: a hardcoded value, or an AI-computed value
    derived from the selected documents' content."""
    from app.models.custom_field import CustomField
    from app.models.custom_field import CUSTOM_FIELD_KINDS
    from app.models.template_group import TemplateGroup
    from app.schemas.onboarding import CustomFieldOut

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template group not found")
    if payload.kind not in CUSTOM_FIELD_KINDS:
        # Quietly turning an unknown kind into "ai" is how a lookup field would have been
        # created as an AI field and then billed a model call per line for a value that is
        # sitting in a spreadsheet.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"{payload.kind!r} is not a kind of field. Use one of: "
                    + ", ".join(sorted(CUSTOM_FIELD_KINDS))),
        )
    cf = CustomField(
        tenant_id=group.tenant_id,
        group_id=group_id,
        label_name=payload.label_name.strip(),
        kind=payload.kind,
        hardcoded_value=payload.hardcoded_value,
        ai_prompt=payload.ai_prompt,
        source_document_ids=payload.source_document_ids or [],
        ask_operator=payload.ask_operator,
        ask_operator_required=payload.ask_operator_required,
        ask_operator_hint=payload.ask_operator_hint,
        per_row=payload.per_row,
        multi_value_from_document=payload.multi_value_from_document,
        lookup_key_label=payload.lookup_key_label,
        lookup_match_columns=payload.lookup_match_columns,
        lookup_return_column=payload.lookup_return_column,
        is_target_value=payload.is_target_value,
        fuzzy_match=payload.fuzzy_match,
        example_value=payload.example_value,
    )
    db.add(cf)
    db.commit()
    db.refresh(cf)
    return CustomFieldOut.model_validate(cf)


@router.patch("/custom-fields/{field_id}")
def edit_custom_field(
    field_id: str,
    payload: CustomFieldEdit,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """Change an existing custom tag — its label, its value, or how it is asked for.

    Renaming is safe: cross-document links reference mark ids, and a job's stored values keep
    whatever label they were extracted under, so history is untouched. Jobs extracted from now
    on carry the new label.
    """
    from app.models.custom_field import CUSTOM_FIELD_KINDS, CustomField
    from app.schemas.onboarding import CustomFieldOut

    cf = db.get(CustomField, field_id)
    if cf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found")
    old_label = cf.label_name
    updates = payload.model_dump(exclude_unset=True)
    # A silent pop is the wrong way to reject a value: "lookup" was dropped here for a while and
    # the request still answered 200, so the wizard's tick box did nothing and there was nothing
    # anywhere to say why. Say what is wrong instead.
    if "kind" in updates and updates["kind"] not in CUSTOM_FIELD_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"{updates['kind']!r} is not a kind of field. Use one of: "
                    + ", ".join(sorted(CUSTOM_FIELD_KINDS))),
        )
    # A lookup reads from the customer's reference sheet, so it has no fixed value of its own -
    # leaving a stale one behind would have it typed into the ERP whenever the sheet has no
    # answer for a line, which is exactly the guess this is meant to avoid.
    if updates.get("kind") == "lookup":
        updates.setdefault("hardcoded_value", None)
    if "label_name" in updates:
        if not (updates["label_name"] or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Label cannot be empty")
        updates["label_name"] = updates["label_name"].strip()
    # NOT NULL columns: an explicit null means "off", never a failed commit.
    for flag in ("ask_operator", "ask_operator_required"):
        if flag in updates and updates[flag] is None:
            updates[flag] = flag == "ask_operator_required"
    if "source_document_ids" in updates and updates["source_document_ids"] is None:
        updates["source_document_ids"] = []
    for field, value in updates.items():
        setattr(cf, field, value)
    new_label = updates.get("label_name")
    if new_label and new_label != old_label and cf.group_id:
        from sqlalchemy.orm.attributes import flag_modified

        from app.core.excel_entry import rename_field_in_excel_config
        from app.models.template_group import TemplateGroup

        group = db.get(TemplateGroup, cf.group_id)
        if group is not None and group.excel_config:
            if rename_field_in_excel_config(group.excel_config, old_label, new_label):
                flag_modified(group, "excel_config")
    db.commit()
    db.refresh(cf)
    return CustomFieldOut.model_validate(cf)


@router.delete("/custom-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_field(
    field_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    from app.models.custom_field import CustomField

    cf = db.get(CustomField, field_id)
    if cf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found")
    db.delete(cf)
    db.commit()


REFERENCE_TEMPLATE_HEADERS = [
    "Match Value 1", "Match Value 2", "Match Value 3", "Match Value 4", "Resolved Value",
]
MAX_REFERENCE_UPLOAD_BYTES = 10 * 1024 * 1024


def _get_lookup_custom_field(db: Session, field_id: str):
    """A custom field allowed to have reference values: kind="lookup" (the original use - a
    material master's own stand-in) or is_target_value (a field that keys on nothing but its
    own extracted value)."""
    from app.models.custom_field import CustomField

    cf = db.get(CustomField, field_id)
    if cf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found")
    if cf.kind != "lookup" and not cf.is_target_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a 'lookup' field, or one ticked \"This is a target value\", learns and stores reference values.",
        )
    return cf


def _get_target_value_mark(db: Session, mark_id: str):
    mark = db.get(FieldMark, mark_id)
    if mark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mark not found")
    if not mark.is_target_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a mark ticked \"This is a target value\" learns and stores reference values.",
        )
    return mark


def _reference_values_response(db: Session, *, custom_field_id: str | None = None, mark_id: str | None = None) -> dict:
    from app.models.custom_field_reference import CustomFieldReferenceValue

    q = db.query(CustomFieldReferenceValue).filter(
        CustomFieldReferenceValue.custom_field_id == custom_field_id,
        CustomFieldReferenceValue.mark_id == mark_id,
    ).order_by(CustomFieldReferenceValue.id)
    return {
        "values": [
            {
                "s_no": r.id,
                "match_value_1": r.match_value_1,
                "match_value_2": r.match_value_2,
                "match_value_3": r.match_value_3,
                "match_value_4": r.match_value_4,
                "resolved_value": r.resolved_value,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in q.all()
        ]
    }


def _download_reference_template(label: str):
    import io

    import openpyxl
    from fastapi.responses import Response

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Reference values"
    ws.append(REFERENCE_TEMPLATE_HEADERS)
    buffer = io.BytesIO()
    wb.save(buffer)
    filename = f"{(label or 'reference').strip().replace(' ', '_')}_reference_template.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _upload_reference_values(
    db: Session, file: UploadFile, *, custom_field_id: str | None = None, mark_id: str | None = None,
) -> dict:
    """Bulk-load reference values from the filled-in template: one row per known identifying
    value(s), already resolved - no need to wait for someone to hit each one on a real job first.

    Only ADDS: a key already on file with a different value is left exactly as it is and
    reported back as a conflict rather than overwritten, since an upload is a second,
    independent source of data landing on top of whatever the table already knows - possibly
    typed in by a person on a real job - and picking one over the other silently would make
    that choice for someone who never got asked. Update PATCH .../reference-values/{id}
    explicitly once a conflict has been looked at, to either apply the uploaded value or leave
    the existing one in place (which needs no call at all)."""
    from app.core.reference_cache import stage_upload_row

    name = (file.filename or "").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=422, detail="Upload an .xlsx or .xlsm workbook (the downloaded template).")
    content = file.file.read(MAX_REFERENCE_UPLOAD_BYTES + 1)
    if len(content) > MAX_REFERENCE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file exceeds the 10 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")

    import io

    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read that workbook: {exc}") from exc
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass

    # Row 1 is the template's own header - skipped rather than validated, the same as the
    # material master upload: an admin who reordered or renamed a column still gets the columns
    # read by position, which is what "download this template and fill it in" means.
    added = unchanged = skipped = 0
    conflicts = []
    for row in rows[1:]:
        cells = [str(c).strip() if c is not None else "" for c in (row or ())][:5]
        cells += [""] * (5 - len(cells))
        match_values, resolved = cells[:4], cells[4]
        staged = stage_upload_row(db, custom_field_id, match_values, resolved, mark_id=mark_id)
        if staged is None:
            skipped += 1
            continue
        if staged["status"] == "added":
            added += 1
        elif staged["status"] == "unchanged":
            unchanged += 1
        else:
            conflicts.append({
                "s_no": staged["row"].id,
                "match_value_1": staged["match_values"][0],
                "match_value_2": staged["match_values"][1],
                "match_value_3": staged["match_values"][2],
                "match_value_4": staged["match_values"][3],
                "existing_value": staged["row"].resolved_value,
                "uploaded_value": staged["uploaded_value"],
            })
    db.commit()
    return {
        "ok": True,
        "rows_added": added,
        "rows_unchanged": unchanged,
        "rows_skipped": skipped,
        # Left exactly as they were - nothing here has been written. Update each one explicitly
        # with PATCH .../reference-values/{s_no} (existing_value/uploaded_value tells you which
        # is which), or leave it: "keep the old value" needs no call at all.
        "conflicts": conflicts,
    }


def _update_reference_value_row(db: Session, value_id: int, payload: dict, *,
                                 custom_field_id: str | None = None, mark_id: str | None = None) -> dict:
    from app.core.reference_cache import update_reference_value
    from app.models.custom_field_reference import CustomFieldReferenceValue

    row = db.query(CustomFieldReferenceValue).filter(
        CustomFieldReferenceValue.id == value_id,
        CustomFieldReferenceValue.custom_field_id == custom_field_id,
        CustomFieldReferenceValue.mark_id == mark_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reference value not found")
    new_value = (payload.get("resolved_value") or "").strip()
    if not new_value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resolved_value is required")
    update_reference_value(db, row, new_value)
    db.commit()
    db.refresh(row)
    return {
        "s_no": row.id, "match_value_1": row.match_value_1, "match_value_2": row.match_value_2,
        "match_value_3": row.match_value_3, "match_value_4": row.match_value_4,
        "resolved_value": row.resolved_value,
    }


def _delete_reference_value_row(db: Session, value_id: int, *,
                                 custom_field_id: str | None = None, mark_id: str | None = None) -> None:
    from app.models.custom_field_reference import CustomFieldReferenceValue

    row = db.query(CustomFieldReferenceValue).filter(
        CustomFieldReferenceValue.id == value_id,
        CustomFieldReferenceValue.custom_field_id == custom_field_id,
        CustomFieldReferenceValue.mark_id == mark_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reference value not found")
    db.delete(row)
    db.commit()


@router.get("/custom-fields/{field_id}/reference-values")
def list_reference_values(
    field_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> dict:
    """Every value learned for this field so far - what the material master (or, for a
    target-value field, nothing) had no answer for, and what a person supplied instead, the
    one time it took someone to answer."""
    _get_lookup_custom_field(db, field_id)
    return _reference_values_response(db, custom_field_id=field_id)


@router.get("/custom-fields/{field_id}/reference-values/template")
def download_reference_values_template(
    field_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    """A blank workbook with the right columns in the right order - fill it in and upload it
    with POST .../reference-values/upload rather than typing rows in one at a time."""
    cf = _get_lookup_custom_field(db, field_id)
    return _download_reference_template(cf.label_name)


@router.post("/custom-fields/{field_id}/reference-values/upload")
def upload_reference_values(
    field_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    _get_lookup_custom_field(db, field_id)
    return _upload_reference_values(db, file, custom_field_id=field_id)


@router.patch("/custom-fields/{field_id}/reference-values/{value_id}")
def update_reference_value_endpoint(
    field_id: str,
    value_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Apply a new value to one existing row - the explicit "update it" choice for a row an
    upload flagged as already present with a different value. Leaving it alone (the "keep the
    old value" choice) needs no call here at all - an upload never writes a conflicting row on
    its own."""
    return _update_reference_value_row(db, value_id, payload, custom_field_id=field_id)


@router.delete("/custom-fields/{field_id}/reference-values/{value_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_reference_value(
    field_id: str,
    value_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    """Remove one wrong learned value - it will simply be asked for again on the next line
    that needs it."""
    _delete_reference_value_row(db, value_id, custom_field_id=field_id)


# --- The same five operations, for a mark instead of a custom field - see FieldMark.is_target_value.
@router.get("/marks/{mark_id}/reference-values")
def list_mark_reference_values(
    mark_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN, TENANT_ADMIN)),
) -> dict:
    _get_target_value_mark(db, mark_id)
    return _reference_values_response(db, mark_id=mark_id)


@router.get("/marks/{mark_id}/reference-values/template")
def download_mark_reference_values_template(
    mark_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
):
    mark = _get_target_value_mark(db, mark_id)
    return _download_reference_template(mark.label_name)


@router.post("/marks/{mark_id}/reference-values/upload")
def upload_mark_reference_values(
    mark_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    _get_target_value_mark(db, mark_id)
    return _upload_reference_values(db, file, mark_id=mark_id)


@router.patch("/marks/{mark_id}/reference-values/{value_id}")
def update_mark_reference_value_endpoint(
    mark_id: str,
    value_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> dict:
    return _update_reference_value_row(db, value_id, payload, mark_id=mark_id)


@router.delete("/marks/{mark_id}/reference-values/{value_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def delete_mark_reference_value(
    mark_id: str,
    value_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    _delete_reference_value_row(db, value_id, mark_id=mark_id)


@router.delete("/cross-doc-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cross_doc_link(
    link_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> None:
    link = db.get(CrossDocLink, link_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    db.delete(link)
    db.commit()


# --------------------------------------------------------------------------- #
# Wizard Demo (Step 5) — test the configured prompts on a DIFFERENT uploaded doc
# --------------------------------------------------------------------------- #
@router.post("/template-documents/{document_id}/test-extract", response_model=DemoResult)
def test_extract(
    document_id: str,
    file: UploadFile = File(...),
    # Opt-in only, for side-by-side verification of the structure engine (app/core/
    # structure_engine.py) before its own settings.structure_engine_enabled flag is ever
    # turned on for real jobs. False (the default, and every real caller's behavior) is
    # today's production reading exactly - the actual "Test extraction" button never sends
    # this, so this parameter changes nothing for it.
    use_structure_engine: bool = False,
    # Same verification-only purpose as use_structure_engine above: returns the exact
    # ocr_text the fields were read from instead of leaving it internal, so a real
    # discrepancy can be inspected directly rather than guessed at from the field values
    # alone. Never sent by the real "Test extraction" button.
    debug: bool = False,
    db: Session = Depends(get_db),
    _=Depends(require_role(SUPER_ADMIN)),
) -> DemoResult:
    """Run this document's configured field prompts against an unseen uploaded file,
    without persisting anything — lets the admin confirm the config generalizes."""
    import fitz  # PyMuPDF

    document = db.get(TemplateDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    marks = db.query(FieldMark).filter(FieldMark.document_id == document_id).all()
    if not marks:
        return DemoResult(document_id=document_id, results=[])

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"File type not allowed — use one of {sorted(ALLOWED_EXTENSIONS)}")
    content = file.file.read(MAX_TEST_BYTES + 1)
    if len(content) > MAX_TEST_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    # The same spec a real job builds - anchor text and example included. Without them this
    # "test it against an unseen document" button tested a DIFFERENT configuration from the one
    # production runs, so a template could pass here and read the wrong field on a real job.
    fields = [field_spec(m) for m in marks]

    tdir = Path(get_settings().uploads_dir) / "_test" / document_id
    try:
        shutil.rmtree(tdir, ignore_errors=True)
        tdir.mkdir(parents=True, exist_ok=True)
        original = tdir / f"test{ext}"
        original.write_bytes(content)
        text_parts: list[str] = []
        image_paths: list[Path] = []
        debug_blocks: list[list[dict]] = []
        with fitz.open(original) as doc:
            for i, page in enumerate(doc, start=1):
                png = tdir / f"page_{i}.png"
                page.get_pixmap(dpi=150).save(png)
                image_paths.append(png)
                try:
                    page_ocr = ocr_page_image(png)
                    text_parts.append(
                        (use_structure_engine and page_ocr.get("structured_text"))
                        or page_ocr.get("layout_text")
                        or page_ocr.get("text", "")
                    )
                    if debug:
                        debug_blocks.append(page_ocr.get("debug_blocks") or [])
                except Exception:  # noqa: BLE001 — OCR (Document AI) unavailable; vision fallback below
                    logger.warning("OCR unavailable for test doc page %s; will try vision fallback", i)
        ocr_text = "\n".join(text_parts)

        if ocr_text.strip():
            extracted = extract_document_fields(ocr_text, fields)
        else:
            extracted = extract_document_fields_from_images(image_paths, fields)
        # LINE ITEMS TOO. This button read single fields only, so a template whose products are
        # multi-value could not be tested against an unseen document at all: every line-item
        # field came back with one value and no rows, and a two-line shipment looked like a
        # one-line one. The demo and a real job both read the table; this is the screen that is
        # supposed to prove the configuration generalises, so it has to read it as well.
        multi = [m for m in marks if m.is_multi_value]
        multi_rows: list[dict] = []
        if multi:
            specs = [field_spec(m, inline_format=False) for m in multi]
            try:
                if ocr_text.strip():
                    multi_rows = extract_document_rows(ocr_text, specs)
                else:
                    multi_rows = extract_document_rows_from_images(image_paths, specs)
            except Exception:  # noqa: BLE001 — a table failure must not lose the single fields
                logger.exception("Test row extraction failed for document %s", document_id)
    except Exception:  # noqa: BLE001
        logger.exception("Test extraction failed for document %s", document_id)
        raise HTTPException(status_code=422, detail="Could not read the uploaded test document.")
    finally:
        shutil.rmtree(tdir, ignore_errors=True)
    results = []
    for m in marks:
        rows = None
        if m.is_multi_value and multi_rows:
            rows = [r.get(m.label_name) for r in multi_rows]
        results.append(
            DemoFieldResult(
                mark_id=m.id,
                label_name=m.label_name,
                # The first row is what the single-value view shows, so a caller that only
                # reads extracted_value still sees something true rather than nothing.
                extracted_value=(rows[0] if rows else extracted.get(m.label_name)),
                extracted_values=rows,
                matched_anchor=m.detected_anchor,
            )
        )
    return DemoResult(
        document_id=document_id,
        results=results,
        debug_text=ocr_text if debug else None,
        debug_blocks=debug_blocks if debug else None,
    )

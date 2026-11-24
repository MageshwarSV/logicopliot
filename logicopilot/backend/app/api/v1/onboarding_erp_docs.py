"""Customer Onboarding - Step 2: Document Mapping (Super Admin).

For each customer document from Step 1 (Additional Details excluded - it has no document): one
sample file, its pages, and a box per Document field drawn where the value sits. The value read
inside the box is shown next to the value the ERP Excel holds, so the admin sees at once
whether the box is on the right spot. Only fields that are drawn get a box; nothing is written
into the live template's own fields here."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.onboarding_erp import ADDITIONAL_ID, _documents, _history, _who, _workbook
from app.core.config import get_settings
from app.core.deps import get_db, require_role, require_write_access
from app.models.erp_mapping import ErpDocSample, ErpField, ErpFieldMark, ErpWorkbook
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/onboarding/erp", tags=["onboarding: document mapping"])

ALLOWED = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 25 * 1024 * 1024


def _sample_dir(workbook_id: str, doc_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", doc_id)[:64]
    return Path(get_settings().uploads_dir) / "onboarding_erp" / workbook_id / "samples" / safe


def _doc(db: Session, wb: ErpWorkbook, doc_id: str) -> dict:
    doc = next((d for d in _documents(db, wb) if d["id"] == doc_id), None)
    if doc is None or doc_id == ADDITIONAL_ID:
        raise HTTPException(status_code=404, detail="No such document for this customer.")
    return doc


def _sample(db: Session, wb_id: str, doc_id: str) -> ErpDocSample | None:
    return (db.query(ErpDocSample)
            .filter(ErpDocSample.workbook_id == wb_id, ErpDocSample.doc_id == doc_id).first())


def _mark_out(m: ErpFieldMark) -> dict:
    return {"field_id": m.field_id, "doc_id": m.doc_id, "page": m.page, "x": m.x, "y": m.y,
            "width": m.width, "height": m.height, "line_item": m.line_item,
            "detected_value": m.detected_value, "anchor": m.anchor}


def _template_sample(db: Session, doc: dict) -> Path | None:
    """The live template's own sample for this document, when it has one."""
    if not doc.get("template_document_id"):
        return None
    from app.models.template_document import TemplateDocument

    td = db.get(TemplateDocument, doc["template_document_id"])
    if td is None or not td.file_path:
        return None
    p = Path(td.file_path)
    return p if p.exists() else None


@router.get("/workbooks/{workbook_id}/doc-mapping")
def doc_mapping(
    workbook_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Every document (with its sample, if any) and its Document fields with their boxes."""
    wb = _workbook(db, workbook_id)
    docs = [d for d in _documents(db, wb) if d["id"] != ADDITIONAL_ID]
    samples = {s.doc_id: s for s in db.query(ErpDocSample).filter(ErpDocSample.workbook_id == wb.id)}
    marks = {m.field_id: m for m in db.query(ErpFieldMark).filter(ErpFieldMark.workbook_id == wb.id)}
    fields = (db.query(ErpField)
              .filter(ErpField.workbook_id == wb.id, ErpField.category == "document")
              .order_by(ErpField.sheet_name, ErpField.position, ErpField.column_index).all())
    out_docs = []
    for d in docs:
        s = samples.get(d["id"])
        out_docs.append({
            "id": d["id"], "name": d["name"],
            "sample": ({"file_name": s.file_name, "page_count": s.page_count,
                        "uploaded_by": s.uploaded_by_name,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else None} if s else None),
            "template_sample_available": _template_sample(db, d) is not None,
        })
    return {
        "workbook": {"id": wb.id, "file_name": wb.file_name, "status": wb.status},
        "documents": out_docs,
        "fields": [{"id": f.id, "sheet_name": f.sheet_name, "column_key": f.column_key,
                    "excel_name": f.excel_name, "display_label": f.display_label,
                    "source_document": f.source_document, "samples": f.samples or [],
                    "data_type": f.data_type, "mandatory": f.mandatory,
                    "mark": _mark_out(marks[f.id]) if f.id in marks else None} for f in fields],
    }


def _store_sample(db: Session, wb: ErpWorkbook, doc: dict, name: str, content: bytes | None,
                  source: Path | None, user: User) -> dict:
    from app.api.v1.template_documents import _render_pages

    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(status_code=422, detail="Upload a PDF, PNG or JPG file.")
    folder = _sample_dir(wb.id, doc["id"])
    tmp = folder.parent / (folder.name + ".incoming")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    original = tmp / f"original{ext}"
    if content is not None:
        original.write_bytes(content)
    else:
        shutil.copyfile(source, original)
    try:
        pages = _render_pages(original, tmp / "pages")
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Could not read that file. Is it a valid PDF or image?") from exc
    shutil.rmtree(folder, ignore_errors=True)
    tmp.rename(folder)

    sample = _sample(db, wb.id, doc["id"])
    replaced = sample is not None
    if sample is None:
        sample = ErpDocSample(workbook_id=wb.id, doc_id=doc["id"], file_name=name, stored_dir="", page_count=0)
        db.add(sample)
    sample.file_name, sample.stored_dir, sample.page_count = name, str(folder), pages
    sample.uploaded_by_name = _who(user)
    # A different sample means different positions: this document's boxes start again.
    cleared = 0
    if replaced:
        cleared = (db.query(ErpFieldMark)
                   .filter(ErpFieldMark.workbook_id == wb.id, ErpFieldMark.doc_id == doc["id"])
                   .delete(synchronize_session=False))
    _history(db, wb, user, "replace_sample" if replaced else "upload_sample",
             detail={"document": doc["name"], "file_name": name, "pages": pages, "boxes_cleared": cleared})
    db.commit()
    return doc_mapping(wb.id, db=db, _=user)


@router.post("/workbooks/{workbook_id}/documents/{doc_id}/sample")
def upload_sample(
    workbook_id: str,
    doc_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """The one sample of this document. Replacing it clears its boxes (they were drawn on the
    old layout)."""
    wb = _workbook(db, workbook_id)
    doc = _doc(db, wb, doc_id)
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file exceeds the 25 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")
    return _store_sample(db, wb, doc, Path(file.filename or "sample.pdf").name, content, None, user)


@router.post("/workbooks/{workbook_id}/documents/{doc_id}/sample/from-template")
def sample_from_template(
    workbook_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Use the sample the live template already has for this document (a copy - the template's
    own file is not touched)."""
    wb = _workbook(db, workbook_id)
    doc = _doc(db, wb, doc_id)
    src = _template_sample(db, doc)
    if src is None:
        raise HTTPException(status_code=404, detail="The template has no sample for this document.")
    return _store_sample(db, wb, doc, f"{doc['name']} (template sample){src.suffix.lower()}", None, src, user)


@router.get("/workbooks/{workbook_id}/documents/{doc_id}/sample/pages/{page}")
def sample_page(
    workbook_id: str,
    doc_id: str,
    page: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
):
    s = _sample(db, workbook_id, doc_id)
    if s is None or page < 1 or page > s.page_count:
        raise HTTPException(status_code=404, detail="Page not found")
    image = Path(s.stored_dir) / "pages" / f"page_{page}.png"
    if not image.exists():
        raise HTTPException(status_code=404, detail="Rendered page missing")
    return FileResponse(image, media_type="image/png")


class MarkIn(BaseModel):
    page: int
    x: float
    y: float
    width: float
    height: float
    line_item: bool = False


def _read_box(stored_dir: str, page: int, box: MarkIn) -> tuple[str | None, str | None]:
    """The text inside the box (and the label next to it), from the page's OCR. Best-effort:
    a box is saved even when OCR is unavailable."""
    try:
        from app.core.anchors import detect_value_and_anchor
        from app.core.docai import get_page_ocr

        ocr = get_page_ocr(Path(stored_dir), page)
        return detect_value_and_anchor(ocr, {"x0": box.x, "y0": box.y,
                                             "x1": box.x + box.width, "y1": box.y + box.height})
    except Exception:  # noqa: BLE001
        return None, None


@router.put("/fields/{field_id}/mark")
def save_mark(
    field_id: str,
    body: MarkIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Draw (or redraw) a Document field's box on its document's sample."""
    f = db.get(ErpField, field_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Field not found")
    if f.category != "document" or not f.source_document:
        raise HTTPException(status_code=422, detail="Only a Document field on a document can be drawn - set it in Step 1.")
    wb = _workbook(db, f.workbook_id)
    doc = _doc(db, wb, f.source_document)
    s = _sample(db, wb.id, doc["id"])
    if s is None:
        raise HTTPException(status_code=422, detail=f"Upload the {doc['name']} sample first.")
    if body.page < 1 or body.page > s.page_count:
        raise HTTPException(status_code=422, detail="That page is not in the sample.")
    for v in (body.x, body.y, body.width, body.height):
        if not 0 <= v <= 1:
            raise HTTPException(status_code=422, detail="Box coordinates must be between 0 and 1.")
    if body.width < 0.003 or body.height < 0.003:
        raise HTTPException(status_code=422, detail="That box is too small.")
    value, anchor = _read_box(s.stored_dir, body.page, body)
    m = db.query(ErpFieldMark).filter(ErpFieldMark.field_id == f.id).first()
    if m is None:
        m = ErpFieldMark(workbook_id=wb.id, field_id=f.id, doc_id=doc["id"], page=body.page,
                         x=body.x, y=body.y, width=body.width, height=body.height)
        db.add(m)
    m.doc_id, m.page, m.x, m.y, m.width, m.height = doc["id"], body.page, body.x, body.y, body.width, body.height
    m.line_item, m.detected_value, m.anchor = body.line_item, value, anchor
    m.updated_by_name = _who(user)
    _history(db, wb, user, "draw_box", field_id=f.id,
             detail={"field": f.excel_name, "document": doc["name"], "page": body.page, "read": value})
    db.commit()
    return {"mark": _mark_out(m)}


class LineItemIn(BaseModel):
    line_item: bool


@router.patch("/fields/{field_id}/mark")
def set_line_item(
    field_id: str,
    body: LineItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    m = db.query(ErpFieldMark).filter(ErpFieldMark.field_id == field_id).first()
    if m is None:
        raise HTTPException(status_code=404, detail="This field has no box yet.")
    m.line_item, m.updated_by_name = body.line_item, _who(user)
    db.commit()
    return {"mark": _mark_out(m)}


@router.delete("/fields/{field_id}/mark")
def clear_mark(
    field_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    m = db.query(ErpFieldMark).filter(ErpFieldMark.field_id == field_id).first()
    if m is not None:
        wb = _workbook(db, m.workbook_id)
        f = db.get(ErpField, field_id)
        _history(db, wb, user, "clear_box", field_id=field_id, detail={"field": f.excel_name if f else field_id})
        db.delete(m)
        db.commit()
    return {"ok": True}


# ---- Import current setup (a template already running on Excel entry) -----------------------

@router.post("/groups/{group_id}/import-current")
def import_current(
    group_id: str,
    replace: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Fill Steps 1, 2 and 4 from the template's own ERP Excel mapping and fields: each column's
    category and document, a box per document field from its existing mark (on the template's
    own samples), and fixed values / operator settings / AI prompts / formulas. Reads the
    template only; writes only the onboarding tables. Replaces this customer's onboarding work,
    so `replace=true` is needed when it already has classified fields."""
    import io

    from starlette.datastructures import UploadFile

    from app.api.v1.onboarding_erp import set_header_row, upload_workbook, HeaderRowIn
    from app.api.v1.template_groups import _excel_dir
    from app.core.erp_field_config import ConfigError, clean_config
    from app.core.erp_import import classify_column, field_index
    from app.core.excel_entry import sheet_plans
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Template not found")
    plans = sheet_plans(group.excel_config or {})
    stored = next(iter(sorted(_excel_dir(group.id).glob("template.*"))), None)
    if not plans or stored is None:
        raise HTTPException(status_code=422, detail="This template has no ERP Excel set-up to import from.")
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group.id).first()
    if wb is not None and not replace:
        done = db.query(ErpField).filter(ErpField.workbook_id == wb.id, ErpField.category.isnot(None)).count()
        if done:
            raise HTTPException(status_code=409, detail=f"This customer already has {done} classified field(s) - importing replaces them.")

    # The template's own ERP workbook, read exactly as an upload would be.
    upload_workbook(group.id, file=UploadFile(file=io.BytesIO(stored.read_bytes()),
                                              filename=f"{group.name} - ERP workbook{stored.suffix}"), db=db, user=user)
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group.id).first()
    for p in plans:
        sheet = next((s for s in wb.sheets if s["name"] == p["sheet"]), None)
        if sheet and sheet["header_row"] != p["header_row"]:
            set_header_row(wb.id, HeaderRowIn(sheet=p["sheet"], header_row=p["header_row"]), db=db, user=user)
    db.refresh(wb)

    tdocs = (db.query(TemplateDocument).filter(TemplateDocument.group_id == group.id)
             .order_by(TemplateDocument.created_at).all())
    doc_order = [d.id for d in tdocs]
    doc_names = {d.id: d.name for d in tdocs}
    wb_doc_ids = {d["id"] for d in _documents(db, wb)}
    marks = db.query(FieldMark).filter(FieldMark.document_id.in_(doc_order)).order_by(FieldMark.created_at).all() if doc_order else []
    cfs = db.query(CustomField).filter(CustomField.group_id == group.id).order_by(CustomField.created_at).all()
    idx = field_index(marks, cfs)
    columns = {p["sheet"]: p["columns"] for p in plans}

    # Start clean: this import is the whole of Steps 1, 2 and 4 for this customer.
    db.query(ErpFieldMark).filter(ErpFieldMark.workbook_id == wb.id).delete(synchronize_session=False)
    fields = db.query(ErpField).filter(ErpField.workbook_id == wb.id, ErpField.is_custom.is_(False)).all()
    counts: dict[str, int] = {}
    missing: list[str] = []
    boxes: list[tuple] = []
    for f in fields:
        for k in ("category", "source_document", "formula", "ai_prompt", "erp_master_ref", "config", "description"):
            setattr(f, k, None)
        cols = columns.get(f.sheet_name) or []
        col = next((c for c in cols if (c.get("header") or "").strip() == f.excel_name.strip()), None) \
            or next((c for c in cols if c.get("column") == f.column_key), None)
        res = classify_column(col, idx, doc_order, doc_names)
        if res.get("description"):
            f.description = res["description"]
        cat = res["category"]
        if cat is None:
            if col and (col.get("field") or "").strip() and not (col.get("field") or "").startswith("#"):
                missing.append(f"{f.sheet_name} · {f.excel_name} → {col.get('field')}")
            continue
        doc = res.get("source_document")
        f.category = cat
        f.source_document = doc if doc in wb_doc_ids else None
        for k in ("formula", "ai_prompt", "erp_master_ref"):
            if res.get(k):
                setattr(f, k, res[k])
        if res.get("display_label"):
            f.display_label = res["display_label"]
        if "mandatory" in res:
            f.mandatory = bool(res["mandatory"])
        if res.get("config") is not None:
            try:
                f.config = clean_config(cat, res["config"])
            except ConfigError:
                f.config = None
            if cat == "fixed" and f.config:
                f.default_value = f.config.get("value") or None
        f.updated_by_name = _who(user)
        counts[cat] = counts.get(cat, 0) + 1
        if cat == "document" and res.get("mark") is not None and f.source_document:
            boxes.append((f, res["mark"], bool(res.get("line_item"))))
    db.commit()

    # Step 2: the template's own samples, and a box per document field from its existing mark.
    samples_used, made = 0, 0
    docs_by_id = {d["id"]: d for d in _documents(db, wb)}
    for doc_id in sorted({f.source_document for f, _m, _l in boxes}):
        if _sample(db, wb.id, doc_id) is None:
            src = _template_sample(db, docs_by_id[doc_id])
            if src is not None:
                _store_sample(db, wb, docs_by_id[doc_id], f"{docs_by_id[doc_id]['name']} (template sample){src.suffix.lower()}",
                              None, src, user)
                samples_used += 1
    for f, mark, line_item in boxes:
        s = _sample(db, wb.id, f.source_document)
        if s is None or mark.page_number > s.page_count:
            continue
        db.add(ErpFieldMark(workbook_id=wb.id, field_id=f.id, doc_id=f.source_document, page=mark.page_number,
                            x=mark.x, y=mark.y, width=mark.width, height=mark.height, line_item=line_item,
                            detected_value=(mark.example_value or None), anchor=(mark.detected_anchor or None),
                            updated_by_name=_who(user)))
        made += 1
    # The fields the template's ERP scripts pick on the ERP screen, not in the Excel (ERP
    # Quotation Name / Branch, ERP Importer Name / Branch ...): the ERP UI sheet.
    from app.api.v1.onboarding_erp import _scripts_for, add_script_fields

    linked = [sc for sc in _scripts_for(db, wb) if group.id in (sc.template_ids or [])]
    erp_ui = add_script_fields(db, wb, group, linked, user) if linked else None
    total = len(fields)
    _history(db, wb, user, "import_current",
             detail={"template": group.name, "classified": sum(counts.values()), "by_category": counts,
                     "left_unclassified": total - sum(counts.values()), "boxes": made, "missing_fields": missing})
    db.commit()
    return {"classified": sum(counts.values()), "by_category": counts, "left_unclassified": total - sum(counts.values()),
            "boxes": made, "samples_used": samples_used, "missing_fields": missing,
            "erp_ui_scripts": [sc.name for sc in linked], "erp_ui_added": (erp_ui or {}).get("added", [])}

"""Customer Onboarding - Step 1: ERP Field Classification (Super Admin).

Upload a customer's ERP Excel workbook; every worksheet and column is read from the file; the
admin classifies each column by its value source (Document, Reference, Fixed, Operator Input,
AI-Derived, Formula / Rule) and configures it. Saved as it changes (a draft) until the admin finishes the step with every column
classified. New tables only - the older Excel entry setup (TemplateGroup.excel_config and its
upload) is not touched."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import get_db, require_role, require_write_access
from app.core.erp_workbook import CATEGORIES, DATA_TYPES, read_workbook, suggest_label
from app.models.erp_mapping import ErpField, ErpMappingHistory, ErpWorkbook
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/onboarding/erp", tags=["onboarding: erp fields"])

MAX_BYTES = 20 * 1024 * 1024
# The always-present tab for values sent to the ERP that are not read off any document
# (operator input, fixed values, reference data, rules) - the job screen's Additional Details.
ADDITIONAL_ID = "additional-details"
ADDITIONAL = {"id": ADDITIONAL_ID, "name": "Additional Details", "template_document_id": None, "fixed": True}
CONFIG_FIELDS = ("display_label", "data_type", "description", "mandatory", "default_value",
                 "validation", "formula", "ai_prompt", "erp_master_ref", "contact_ref", "source_document", "config")


def _who(user: User) -> str:
    return user.full_name or user.email


def _dir(workbook_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "onboarding_erp" / workbook_id


def _history(db: Session, wb: ErpWorkbook, user: User, action: str, field_id: str | None = None,
             detail: dict | None = None) -> None:
    db.add(ErpMappingHistory(workbook_id=wb.id, group_id=wb.group_id, field_id=field_id,
                             action=action, detail=detail, user_name=_who(user)))


def _field_out(f: ErpField) -> dict:
    return {"id": f.id, "sheet_name": f.sheet_name, "column_key": f.column_key,
            "column_index": f.column_index, "excel_name": f.excel_name, "is_custom": f.is_custom,
            "category": f.category, "position": f.position,
            **{k: getattr(f, k) for k in CONFIG_FIELDS}, "samples": f.samples or []}


def _summary(fields: list[ErpField]) -> dict:
    cols = [f for f in fields if not f.is_custom]
    done = sum(1 for f in cols if f.category)
    total = len(cols)
    return {"total": total, "classified": done, "unclassified": total - done,
            "percent": round(done * 100 / total) if total else 0}


def _documents(db: Session, wb: ErpWorkbook) -> list[dict]:
    """The customer's documents (Document bucket tabs). Seeded from the template's own
    documents the first time; after that the admin's list."""
    if wb.documents is None:
        from app.models.template_document import TemplateDocument

        docs = (db.query(TemplateDocument).filter(TemplateDocument.group_id == wb.group_id)
                .order_by(TemplateDocument.created_at).all())
        wb.documents = [{"id": d.id, "name": d.name, "template_document_id": d.id} for d in docs]
        db.commit()
    docs = list(wb.documents or [])
    # Additional Details is always there, always last.
    if not any(d["id"] == ADDITIONAL_ID for d in docs):
        wb.documents = [*docs, ADDITIONAL]
        db.commit()
        docs = list(wb.documents)
    return [d for d in docs if d["id"] != ADDITIONAL_ID] + [ADDITIONAL]


def _workbook_out(db: Session, wb: ErpWorkbook) -> dict:
    documents = _documents(db, wb)
    fields = (db.query(ErpField).filter(ErpField.workbook_id == wb.id)
              .order_by(ErpField.sheet_name, ErpField.position, ErpField.column_index).all())
    group = db.get(TemplateGroup, wb.group_id)
    return {"id": wb.id, "group_id": wb.group_id, "customer": group.name if group else "",
            "file_name": wb.file_name, "status": wb.status, "documents": documents,
            "sheets": [{"name": s["name"], "header_row": s["header_row"],
                        "column_count": len(s["columns"])} for s in wb.sheets],
            "fields": [_field_out(f) for f in fields], "summary": _summary(fields),
            "updated_at": wb.updated_at.isoformat() if wb.updated_at else None,
            "updated_by": wb.updated_by_name}


def _workbook(db: Session, workbook_id: str) -> ErpWorkbook:
    wb = db.get(ErpWorkbook, workbook_id)
    if wb is None:
        raise HTTPException(status_code=404, detail="Workbook not found")
    return wb


def _sync_fields(db: Session, wb: ErpWorkbook, sheets: list[dict], previous: list[ErpField]) -> int:
    """Creates one field per column. A column whose sheet + heading existed before keeps its
    classification and configuration (a re-upload or a header-row fix must not lose work);
    custom fields of a sheet that still exists are kept. Returns how many were carried over."""
    old = {(f.sheet_name, f.excel_name.strip().lower()): f for f in previous if not f.is_custom}
    carried = 0
    sheet_names = {s["name"] for s in sheets}
    for s in sheets:
        for col in s["columns"]:
            name = col["name"] or f"(no heading) {col['key']}"
            prev = old.pop((s["name"], name.strip().lower()), None)
            f = ErpField(workbook_id=wb.id, group_id=wb.group_id, sheet_name=s["name"],
                         column_key=col["key"], column_index=col["index"], excel_name=name,
                         is_custom=False, samples=col["samples"],
                         data_type=col["suggested_type"], display_label=suggest_label(name),
                         position=col["index"])
            if prev is not None:
                carried += 1
                f.category, f.position = prev.category, prev.position
                for k in CONFIG_FIELDS:
                    setattr(f, k, getattr(prev, k))
            db.add(f)
    for f in previous:
        if f.is_custom and f.sheet_name in sheet_names:
            db.add(ErpField(workbook_id=wb.id, group_id=wb.group_id, sheet_name=f.sheet_name,
                            excel_name=f.excel_name, is_custom=True, category=f.category,
                            position=f.position, samples=None,
                            **{k: getattr(f, k) for k in CONFIG_FIELDS}))
    for f in previous:
        db.delete(f)
    return carried


@router.get("/customers")
def list_customers(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """Customer templates, with their ERP workbook's progress when one was uploaded."""
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    wbs = {w.group_id: w for w in db.query(ErpWorkbook).all()}
    out = []
    for g in db.query(TemplateGroup).order_by(TemplateGroup.name).all():
        w = wbs.get(g.id)
        from app.core.excel_entry import sheet_plans

        item = {"group_id": g.id, "name": g.name, "tenant": tenants.get(g.tenant_id, ""),
                "mode": g.mode, "workbook": None,
                # Runs on Excel entry already: Step 0 can import its current set-up.
                "excel_setup": bool(sheet_plans(g.excel_config or {}))}
        if w is not None:
            fields = db.query(ErpField).filter(ErpField.workbook_id == w.id).all()
            item["workbook"] = {"id": w.id, "file_name": w.file_name, "status": w.status,
                                "sheet_count": len(w.sheets), **_summary(fields)}
        out.append(item)
    return out


@router.get("/groups/{group_id}/workbook")
def get_workbook(
    group_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict | None:
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group_id).first()
    return _workbook_out(db, wb) if wb else None


@router.post("/groups/{group_id}/workbook")
def upload_workbook(
    group_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Read every worksheet and column of the customer's ERP workbook. Uploading again
    replaces the workbook and keeps every classification whose sheet + heading still exists."""
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Customer template not found")
    name = Path(file.filename or "").name.strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="Upload an .xlsx or .xlsm workbook (an old .xls cannot be read).")
    content = file.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The workbook exceeds the 20 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")

    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group_id).first()
    replacing = wb is not None
    if wb is None:
        wb = ErpWorkbook(tenant_id=group.tenant_id, group_id=group_id, file_name=name,
                         stored_path="", sheets=[], status="draft", uploaded_by_name=_who(user))
        db.add(wb)
        db.flush()
    folder = _dir(wb.id)
    tmp = folder / "incoming"
    tmp.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:200] or "workbook.xlsx"
    incoming = tmp / safe
    incoming.write_bytes(content)
    try:
        sheets = read_workbook(incoming)
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"That workbook could not be read: {exc}") from exc
    if not any(s["columns"] for s in sheets):
        db.rollback()
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=422, detail="No column headings were found in any worksheet.")

    # The uploaded file is kept exactly as uploaded, never modified.
    for old in folder.glob("*.xls*"):
        old.unlink(missing_ok=True)
    final = folder / safe
    shutil.move(str(incoming), final)
    shutil.rmtree(tmp, ignore_errors=True)

    previous = db.query(ErpField).filter(ErpField.workbook_id == wb.id).all()
    carried = _sync_fields(db, wb, sheets, previous)
    wb.file_name, wb.stored_path, wb.sheets = name, str(final), sheets
    wb.status, wb.updated_by_name = "draft", _who(user)
    _history(db, wb, user, "replace_workbook" if replacing else "upload_workbook",
             detail={"file_name": name, "sheets": len(sheets), "carried_over": carried})
    db.commit()
    return _workbook_out(db, wb)


class HeaderRowIn(BaseModel):
    sheet: str
    header_row: int


@router.post("/workbooks/{workbook_id}/header-row")
def set_header_row(
    workbook_id: str,
    body: HeaderRowIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """The row holding a sheet's headings, when the detected one is wrong (a title above)."""
    wb = _workbook(db, workbook_id)
    if body.header_row < 1 or body.header_row > 200:
        raise HTTPException(status_code=422, detail="The heading row must be between 1 and 200.")
    if body.sheet not in [s["name"] for s in wb.sheets]:
        raise HTTPException(status_code=404, detail="No such worksheet in this workbook.")
    overrides = {s["name"]: s["header_row"] for s in wb.sheets}
    overrides[body.sheet] = body.header_row
    try:
        sheets = read_workbook(wb.stored_path, header_rows=overrides)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"The workbook could not be read again: {exc}") from exc
    previous = db.query(ErpField).filter(ErpField.workbook_id == wb.id).all()
    _sync_fields(db, wb, sheets, previous)
    wb.sheets, wb.updated_by_name = sheets, _who(user)
    _history(db, wb, user, "header_row", detail={"sheet": body.sheet, "header_row": body.header_row})
    db.commit()
    return _workbook_out(db, wb)


class FieldPatch(BaseModel):
    category: str | None = None
    position: int | None = None
    display_label: str | None = None
    data_type: str | None = None
    description: str | None = None
    mandatory: bool | None = None
    default_value: str | None = None
    validation: str | None = None
    formula: str | None = None
    ai_prompt: str | None = None
    erp_master_ref: str | None = None
    contact_ref: str | None = None
    source_document: str | None = None
    config: dict | None = None


def _check(updates: dict) -> None:
    if updates.get("category") not in (None, *CATEGORIES):
        raise HTTPException(status_code=422, detail=f"Category must be one of: {', '.join(CATEGORIES)}.")
    if "data_type" in updates and updates["data_type"] not in DATA_TYPES:
        raise HTTPException(status_code=422, detail=f"Data type must be one of: {', '.join(DATA_TYPES)}.")


@router.patch("/fields/{field_id}")
def update_field(
    field_id: str,
    body: FieldPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Classify (category; null = back to unclassified), reorder (position), or configure."""
    f = db.get(ErpField, field_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Field not found")
    wb = _workbook(db, f.workbook_id)
    updates = body.model_dump(exclude_unset=True)
    _check(updates)
    if updates.get("source_document") and updates["source_document"] not in {
            d["id"] for d in _documents(db, wb)}:
        raise HTTPException(status_code=422, detail="That document is not in this customer's list.")
    # Every classified field belongs to one of the customer's documents (the tab it sits on);
    # taken out of its bucket, it belongs to none.
    if "category" in updates and updates["category"] is None:
        updates["source_document"] = None
    if "config" in updates:
        from app.core.erp_field_config import ConfigError, clean_config

        try:
            updates["config"] = clean_config(updates.get("category", f.category), updates["config"])
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        # A Fixed field's value is also its default value (what earlier screens show).
        if updates.get("category", f.category) == "fixed" and updates["config"]:
            updates["default_value"] = updates["config"]["value"] or None
    before = {k: getattr(f, k) for k in updates}
    for k, v in updates.items():
        if isinstance(v, str) and k not in ("category",):
            v = v.strip() or None
            if k == "data_type" and v is None:
                continue
        setattr(f, k, v)
    f.updated_by_name = _who(user)
    wb.updated_by_name = _who(user)
    changed = {k: {"from": before[k], "to": getattr(f, k)} for k in updates if before[k] != getattr(f, k)}
    if changed:
        _history(db, wb, user, "classify" if set(changed) <= {"category", "position"} else "configure",
                 field_id=f.id, detail={"field": f.excel_name, "sheet": f.sheet_name, **changed})
    db.commit()
    return {"field": _field_out(f), "summary": _summary(
        db.query(ErpField).filter(ErpField.workbook_id == wb.id).all()), "status": wb.status}


class CustomFieldIn(BaseModel):
    sheet: str
    name: str
    category: str
    source_document: str | None = None


@router.post("/workbooks/{workbook_id}/fields")
def add_custom_field(
    workbook_id: str,
    body: CustomFieldIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """A field the workbook has no column for, added to a sheet straight into a bucket."""
    wb = _workbook(db, workbook_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the field a name.")
    if body.sheet not in [s["name"] for s in wb.sheets]:
        raise HTTPException(status_code=404, detail="No such worksheet in this workbook.")
    _check({"category": body.category})
    taken = {f.excel_name.lower() for f in db.query(ErpField).filter(
        ErpField.workbook_id == wb.id, ErpField.sheet_name == body.sheet)}
    if name.lower() in taken:
        raise HTTPException(status_code=422, detail=f"'{name}' already exists on {body.sheet}.")
    f = ErpField(workbook_id=wb.id, group_id=wb.group_id, sheet_name=body.sheet, excel_name=name,
                 is_custom=True, category=body.category, position=10_000,
                 display_label=suggest_label(name), data_type="text", updated_by_name=_who(user),
                 source_document=body.source_document)
    db.add(f)
    db.flush()
    _history(db, wb, user, "add_custom_field", field_id=f.id,
             detail={"field": name, "sheet": body.sheet, "category": body.category})
    db.commit()
    return {"field": _field_out(f)}


class DocumentIn(BaseModel):
    name: str


def _doc_name(db: Session, wb: ErpWorkbook, name: str, skip_id: str | None = None) -> str:
    clean = " ".join(name.split())
    if not clean:
        raise HTTPException(status_code=422, detail="Give the document a name.")
    if len(clean) > 100:
        raise HTTPException(status_code=422, detail="Document name is too long (max 100).")
    if any(d["name"].lower() == clean.lower() and d["id"] != skip_id for d in _documents(db, wb)):
        raise HTTPException(status_code=422, detail=f"There is already a document called '{clean}'.")
    return clean


@router.post("/workbooks/{workbook_id}/documents")
def add_document(
    workbook_id: str,
    body: DocumentIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """A document tab in the Document bucket (e.g. a Bill of Lading the template lacks)."""
    import uuid

    wb = _workbook(db, workbook_id)
    name = _doc_name(db, wb, body.name)
    doc = {"id": str(uuid.uuid4()), "name": name, "template_document_id": None}
    docs = _documents(db, wb)
    wb.documents = [*docs[:-1], doc, docs[-1]]      # new documents go before Additional Details
    _history(db, wb, user, "add_document", detail={"document": name})
    db.commit()
    return _workbook_out(db, wb)


@router.patch("/workbooks/{workbook_id}/documents/{doc_id}")
def rename_document(
    workbook_id: str,
    doc_id: str,
    body: DocumentIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    wb = _workbook(db, workbook_id)
    docs = _documents(db, wb)
    if not any(d["id"] == doc_id for d in docs):
        raise HTTPException(status_code=404, detail="Document not found")
    if doc_id == ADDITIONAL_ID:
        raise HTTPException(status_code=422, detail="Additional Details is always there and keeps its name.")
    name = _doc_name(db, wb, body.name, skip_id=doc_id)
    wb.documents = [{**d, "name": name} if d["id"] == doc_id else d for d in docs]
    _history(db, wb, user, "rename_document", detail={"document": name})
    db.commit()
    return _workbook_out(db, wb)


@router.delete("/workbooks/{workbook_id}/documents/{doc_id}")
def remove_document(
    workbook_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Removes the tab; its fields stay Document fields, with no document assigned."""
    wb = _workbook(db, workbook_id)
    docs = _documents(db, wb)
    doc = next((d for d in docs if d["id"] == doc_id), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc_id == ADDITIONAL_ID:
        raise HTTPException(status_code=422, detail="Additional Details cannot be removed.")
    moved = 0
    for f in db.query(ErpField).filter(ErpField.workbook_id == wb.id, ErpField.source_document == doc_id):
        f.source_document = None
        moved += 1
    wb.documents = [d for d in docs if d["id"] != doc_id]
    _history(db, wb, user, "remove_document", detail={"document": doc["name"], "fields_unassigned": moved})
    db.commit()
    return _workbook_out(db, wb)


@router.delete("/fields/{field_id}")
def delete_custom_field(
    field_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Only a custom field can be deleted - an Excel column always exists in the workbook."""
    f = db.get(ErpField, field_id)
    if f is None:
        raise HTTPException(status_code=404, detail="Field not found")
    if not f.is_custom:
        raise HTTPException(status_code=422, detail="An Excel column cannot be deleted - drag it out of its bucket instead.")
    wb = _workbook(db, f.workbook_id)
    _history(db, wb, user, "delete_custom_field", field_id=f.id,
             detail={"field": f.excel_name, "sheet": f.sheet_name})
    db.delete(f)
    db.commit()
    return {"ok": True}


class StatusIn(BaseModel):
    status: str      # draft | complete


@router.post("/workbooks/{workbook_id}/status")
def set_status(
    workbook_id: str,
    body: StatusIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Save as draft, or finish Step 1. Columns left unclassified are simply not used (most
    of an ERP workbook is never filled - 314 of 423 columns in 4S's Sea/Air Import), so they do
    not block finishing; at least one classified field is needed."""
    wb = _workbook(db, workbook_id)
    if body.status not in ("draft", "complete"):
        raise HTTPException(status_code=422, detail="Status must be draft or complete.")
    s = _summary(db.query(ErpField).filter(ErpField.workbook_id == wb.id).all())
    if body.status == "complete" and not s["classified"]:
        raise HTTPException(status_code=422, detail="Classify at least one field before going on to Step 2.")
    wb.status, wb.updated_by_name = body.status, _who(user)
    _history(db, wb, user, "complete" if body.status == "complete" else "save_draft",
             detail={"classified": s["classified"], "left_unclassified": s["unclassified"]})
    db.commit()
    return _workbook_out(db, wb)


@router.get("/workbooks/{workbook_id}/history")
def history(
    workbook_id: str,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    rows = (db.query(ErpMappingHistory).filter(ErpMappingHistory.workbook_id == workbook_id)
            .order_by(ErpMappingHistory.created_at.desc()).limit(min(limit, 500)).all())
    return [{"action": r.action, "detail": r.detail, "user": r.user_name,
             "at": r.created_at.isoformat() if r.created_at else None, "field_id": r.field_id}
            for r in rows]

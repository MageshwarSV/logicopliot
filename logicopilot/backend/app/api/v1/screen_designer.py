"""Super Admin -> Screen Designer: a template document's job-screen layout.

Placement only (sections, their order, the product card) - see app.models.screen_layout and
app.core.screen_layout. Saves straight away, and every save is kept in screen_layout_history.
A document with no layout keeps the job screen's existing behaviour."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role, require_write_access
from app.core.screen_layout import LayoutError, allowed_refs, build_from_current, clean_layout
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job
from app.models.screen_layout import ScreenLayout, ScreenLayoutHistory
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/screen-designer", tags=["screen designer"])


class LayoutIn(BaseModel):
    layout: dict


def _document(db: Session, document_id: str) -> TemplateDocument:
    doc = db.get(TemplateDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _fields(db: Session, doc: TemplateDocument) -> tuple[list, list]:
    marks = db.query(FieldMark).filter(FieldMark.document_id == doc.id).all()
    cfs = db.query(CustomField).filter(CustomField.group_id == doc.group_id).all()
    return marks, cfs


def _out(row: ScreenLayout | None, dropped: list[str] | None = None) -> dict:
    if row is None:
        return {"layout": None, "updated_at": None, "updated_by": None}
    out = {"layout": row.layout, "updated_at": row.updated_at.isoformat() if row.updated_at else None,
           "updated_by": row.updated_by_name}
    if dropped:
        out["dropped"] = dropped
    return out


def _save(db: Session, doc: TemplateDocument, layout: dict, user: User, action: str) -> ScreenLayout:
    row = db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first()
    name = user.full_name or user.email
    if row is None:
        row = ScreenLayout(tenant_id=doc.tenant_id, group_id=doc.group_id,
                           template_document_id=doc.id, layout=layout)
        db.add(row)
    row.layout = layout
    row.updated_by_id, row.updated_by_name = user.id, name
    db.add(ScreenLayoutHistory(template_document_id=doc.id, layout=layout, action=action,
                               saved_by_id=user.id, saved_by_name=name))
    db.commit()
    db.refresh(row)
    return row


@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """Every template with its documents, and which documents have a layout."""
    with_layout = {d for (d,) in db.query(ScreenLayout.template_document_id).all()}
    out = []
    for g in db.query(TemplateGroup).order_by(TemplateGroup.name).all():
        docs = (db.query(TemplateDocument).filter(TemplateDocument.group_id == g.id)
                .order_by(TemplateDocument.created_at).all())
        out.append({"id": g.id, "name": g.name, "mode": g.mode, "tenant_id": g.tenant_id,
                    "documents": [{"id": d.id, "name": d.name, "doc_type": d.doc_type,
                                   "has_layout": d.id in with_layout} for d in docs]})
    return out


@router.get("/documents/{document_id}/layout")
def get_layout(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    doc = _document(db, document_id)
    return _out(db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first())


@router.put("/documents/{document_id}/layout")
def save_layout(
    document_id: str,
    body: LayoutIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    doc = _document(db, document_id)
    marks, cfs = _fields(db, doc)
    section_refs, product_refs = allowed_refs(marks, cfs)
    try:
        layout, dropped = clean_layout(body.layout, section_refs, product_refs)
    except LayoutError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(_save(db, doc, layout, user, "save"), dropped)


@router.post("/documents/{document_id}/layout/from-current")
def create_from_current(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Saves the layout this document's job screen shows today - the starting point."""
    doc = _document(db, document_id)
    marks, cfs = _fields(db, doc)
    layout = build_from_current(marks, cfs, doc.id)
    section_refs, product_refs = allowed_refs(marks, cfs)
    layout, _dropped = clean_layout(layout, section_refs, product_refs)
    return _out(_save(db, doc, layout, user, "create_from_current"))


@router.delete("/documents/{document_id}/layout")
def remove_layout(
    document_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Back to the default screen. Kept in the history, so it can be restored."""
    doc = _document(db, document_id)
    row = db.query(ScreenLayout).filter(ScreenLayout.template_document_id == doc.id).first()
    if row is not None:
        db.delete(row)
        db.add(ScreenLayoutHistory(template_document_id=doc.id, layout=None, action="remove",
                                   saved_by_id=user.id, saved_by_name=user.full_name or user.email))
        db.commit()
    return _out(None)


@router.get("/documents/{document_id}/fields")
def list_fields(
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    """Every field the designer can place for this document, with the properties its panel
    edits. place = "section" (one value per document, or a job-level field) or "card" (one
    per product line). Edits go to the field itself (PATCH /marks/{id}, /custom-fields/{id})."""
    from app.core.screen_layout import custom_ref, header_marks, mark_ref, product_marks

    doc = _document(db, document_id)
    marks, cfs = _fields(db, doc)

    def common(obj) -> dict:
        return {"id": obj.id, "label_name": obj.label_name, "display_label": obj.display_label,
                "full_width": bool(obj.full_width), "multiline": bool(obj.multiline),
                "mandatory": bool(obj.ask_operator_required),
                "fixed_options": obj.fixed_options, "dropdown_style": obj.dropdown_style}

    fields = []
    for m in sorted(header_marks(marks), key=lambda m: m.created_at):
        fields.append({**common(m), "ref": mark_ref(m.id), "source": "mark", "place": "section",
                       "on_document": bool(m.width and m.height)})
    for m in sorted(product_marks(marks), key=lambda m: m.created_at):
        fields.append({**common(m), "ref": mark_ref(m.id), "source": "mark", "place": "card",
                       "on_document": bool(m.width and m.height)})
    for c in sorted(cfs, key=lambda c: c.created_at):
        if c.per_row or c.hidden_from_capture:
            continue
        fields.append({**common(c), "ref": custom_ref(c.id), "source": "custom", "place": "section",
                       "kind": c.kind, "on_this_tab": c.display_document_id == doc.id})
    return {"document": {"id": doc.id, "name": doc.name, "doc_type": doc.doc_type,
                         "group_id": doc.group_id},
            "fields": fields}


@router.get("/groups/{group_id}/recent-jobs")
def recent_jobs(
    group_id: str,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> list[dict]:
    """The template's latest jobs, for the designer's preview picker."""
    rows = (db.query(Job.id, Job.reference, Job.status, Job.created_at)
            .filter(Job.group_id == group_id).order_by(Job.created_at.desc()).limit(limit).all())
    return [{"id": r.id, "reference": r.reference, "status": r.status,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


# ---- Safe rename of a field's parameter (internal) name ------------------------------------
#
# The parameter name is what the ERP Excel mapping, ERP scripts, formulas, lookups and every
# job's saved values use. A rename is allowed only when nothing that cannot be updated with it
# depends on it: the Excel mapping's plain column links are rewritten automatically; anything
# else (an ERP script step, another field's formula/lookup, a column's rule/fallback/
# placeholder, special screen behaviour, a job still in progress) refuses it, with the reason.

# Field names the operator screen / backend treat specially (combo boxes, the quotation trio,
# insurance, CTH, consignee lookups ...). Renaming one would silently switch that behaviour off.
PROTECTED_NAMES = {
    "AdvancePriorNormal", "BETypeCode", "Combined Description", "Consignee", "Consignment Type",
    "CTH", "Currency_type", "CustomsHouseCode", "ERP Branch Address", "ERP Consignee Country",
    "ERP Consignee Name", "ERP Importer Address / Branch Code", "Erp Entry Name", "Frt_Currency",
    "Gross Weight Unit", "HBL Date", "IGM No", "Ins_Currency", "MBL Date", "MBL No",
    "Misc_Charge_Currency", "Package Unit", "Payment Method", "PortOfShipmentCode",
    "Product Description Language", "Product Value", "Quotation Name", "Shipment City",
    "Shipment Country", "Shipment Port Code", "Shipper Address", "Supplier Address",
    "TransportModeCode", "branch_name", "consignee_full_name", "container_number",
    "insurance_amount", "insurance_mode", "insurance_percentage", "mode_of_transport",
    "product_description", "quotation_address",
}
MAX_NAME = 100


class RenameIn(BaseModel):
    group_id: str
    ref: str
    new_name: str


def _ref_target(db: Session, group_id: str, ref: str) -> tuple[str, list[FieldMark], CustomField | None]:
    """(current name, the marks carrying it on every document of the template, or the custom field)."""
    kind, _, rid = ref.partition(":")
    if kind == "m":
        mark = db.get(FieldMark, rid)
        if mark is None:
            raise HTTPException(status_code=404, detail="Field not found")
        doc_ids = [d for (d,) in db.query(TemplateDocument.id).filter(TemplateDocument.group_id == group_id)]
        if mark.document_id not in doc_ids:
            raise HTTPException(status_code=404, detail="Field is not on this template")
        same = (db.query(FieldMark).filter(FieldMark.document_id.in_(doc_ids),
                                           FieldMark.label_name == mark.label_name).all())
        return mark.label_name, same, None
    if kind == "c":
        cf = db.get(CustomField, rid)
        if cf is None or cf.group_id != group_id:
            raise HTTPException(status_code=404, detail="Field not found")
        return cf.label_name, [], cf
    raise HTTPException(status_code=422, detail="Unknown field reference")


def _usage(db: Session, group_id: str, name: str) -> dict:
    """Everywhere the template depends on this name, split into what a rename updates
    automatically and what blocks it."""
    import json
    import re

    from app.api.v1.jobs import _job_category
    from app.core.excel_entry import sheet_plans
    from app.models.erp_script import ErpScript
    from app.models.job import JobFieldValue

    group = db.get(TemplateGroup, group_id)
    auto, blocks = [], []
    if name in PROTECTED_NAMES:
        blocks.append("The operator screen has special behaviour for this exact name (it would stop working).")
    for p in sheet_plans((group.excel_config if group else None) or {}):
        for c in p["columns"]:
            where = f"{p['sheet']} · {c.get('header') or c.get('column')}"
            if (c.get("field") or "").strip() == name:
                auto.append(f"ERP Excel column {where} (updated with the rename)")
            pick = c.get("pick_by") or {}
            if pick and (pick.get("field") == name or name in (pick.get("options") or {}).values()):
                blocks.append(f"ERP Excel column {where} chooses between fields by this name.")
            if name in (c.get("fallback_fields") or []):
                blocks.append(f"ERP Excel column {where} falls back to this field.")
            for v in (c.get("values") or [c.get("value")]):
                if f"[[{name}]]" in str(v or ""):
                    blocks.append(f"ERP Excel column {where} inserts this field's value.")
    for script in db.query(ErpScript).all():
        if group_id not in (script.template_ids or []):
            continue
        text = json.dumps(script.steps or [])
        if re.search(r'"field_labels?"\s*:\s*(\[[^\]]*)?"' + re.escape(name) + '"', text) \
                or (script.branch_field or "") == name:
            blocks.append(f"ERP script '{script.name}' types this field.")
    for c in db.query(CustomField).filter(CustomField.group_id == group_id).all():
        if c.label_name == name:
            continue
        pieces = [p for p in (c.composite_source_labels or []) if isinstance(p, str)]
        keys = [k.strip() for k in (c.lookup_key_label or "").split(",") if k.strip()]
        if name in pieces:
            blocks.append(f"Field '{c.label_name}' joins this field into its value.")
        if name in keys:
            blocks.append(f"Field '{c.label_name}' looks values up by this field.")
    in_progress = 0
    job_ids = {j for (j,) in db.query(JobFieldValue.job_id).filter(JobFieldValue.label_name == name).distinct()}
    if job_ids:
        for job in db.query(Job).filter(Job.group_id == group_id, Job.id.in_(job_ids)).all():
            if _job_category(job) != "submitted":
                in_progress += 1
    if in_progress:
        blocks.append(f"{in_progress} job(s) of this template still in progress have values saved under this name.")
    return {"name": name, "auto": auto, "blocks": blocks, "allowed": not blocks}


@router.get("/fields/usage")
def field_usage(
    group_id: str,
    ref: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    name, _marks, _cf = _ref_target(db, group_id, ref)
    return _usage(db, group_id, name)


@router.post("/fields/rename")
def rename_parameter(
    body: RenameIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_write_access(SUPER_ADMIN)),
) -> dict:
    """Rename a field's parameter name - only when nothing that cannot follow depends on it.
    Every copy of a document field across the template's documents is renamed together, and
    the ERP Excel mapping's column links are rewritten in the same transaction."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.excel_entry import rename_field_in_excel_config

    old, marks, cf = _ref_target(db, body.group_id, body.ref)
    new = " ".join(body.new_name.split())
    if not new:
        raise HTTPException(status_code=422, detail="Give the parameter a name.")
    if len(new) > MAX_NAME:
        raise HTTPException(status_code=422, detail=f"Parameter names are at most {MAX_NAME} characters.")
    if new == old:
        return {"name": old, "renamed": 0}
    doc_ids = [d for (d,) in db.query(TemplateDocument.id).filter(TemplateDocument.group_id == body.group_id)]
    taken = {m for (m,) in db.query(FieldMark.label_name).filter(FieldMark.document_id.in_(doc_ids))}
    taken |= {c for (c,) in db.query(CustomField.label_name).filter(CustomField.group_id == body.group_id)}
    if new in taken or new in PROTECTED_NAMES:
        raise HTTPException(status_code=422, detail=f"'{new}' is already used on this template.")
    usage = _usage(db, body.group_id, old)
    if not usage["allowed"]:
        raise HTTPException(status_code=409, detail="Not renamed: " + " ".join(usage["blocks"]))
    for m in marks:
        m.label_name = new
    if cf is not None:
        cf.label_name = new
    group = db.get(TemplateGroup, body.group_id)
    if group is not None and group.excel_config and rename_field_in_excel_config(group.excel_config, old, new):
        flag_modified(group, "excel_config")
    db.commit()
    return {"name": new, "renamed": len(marks) + (1 if cf is not None else 0), "excel_updated": len(usage["auto"])}


def layouts_for_group(db: Session, group_id: str) -> dict[str, dict]:
    """{template_document_id: layout} for a template's documents that have one - sent with the
    job detail so the job screen can use it."""
    return {r.template_document_id: r.layout
            for r in db.query(ScreenLayout).filter(ScreenLayout.group_id == group_id).all()}

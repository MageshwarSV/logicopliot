"""Customer Onboarding -> the REAL template (Super Admin).

The onboarding steps (ERP Field Mapping, Document Mapping, Field Configuration) keep their own
tables. These endpoints connect them to the live template that jobs actually run on, so every
setting the old Document Templates / Customer Template Mapping screens hold can be edited from
the new screens too - without removing or changing those screens:

  * links      - which live field (FieldMark / CustomField) each onboarding field fills, worked
                 out from the template's ERP Excel mapping (column heading -> field), an ERP UI
                 field by its own name, otherwise by a name match (suggested only)
  * Excel rules - one column / one sheet of TemplateGroup.excel_config, every key excel_entry
                 honours (field, value, values, pick_by, from_document, fallback_fields,
                 transform, max_len; sheet scope + header row)
  * create     - a live box (FieldMark) from a Document Mapping box, or a live custom field
                 from a Field Configuration field, linked to its Excel column in one go
  * template-level settings that had no API or no screen: document order, operators assigned
    to the template, prompt change history, classifier document samples.

Every edit of the live field itself goes through the existing routes (PATCH /marks/{id},
PATCH /custom-fields/{id}, reference values, cross-doc links ...), exactly as the old screens.
"""
from __future__ import annotations

import copy
import hashlib
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.v1.onboarding_erp import ADDITIONAL_ID, _documents, _history
from app.core.deps import get_db, require_role, require_write_access
from app.core.erp_ui_fields import ERP_UI_SHEET
from app.models.custom_field import CustomField
from app.models.erp_mapping import ErpDocSample, ErpField, ErpWorkbook
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/onboarding/template", tags=["onboarding: live template"])

# Every per-column key app.core.excel_entry reads.
RULE_KEYS = ("field", "value", "values", "pick_by", "from_document", "fallback_fields", "transform", "max_len",
             "ai_map")
SCOPES = ("job", "line", "invoice", "fixed")


def _n(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _group(db: Session, group_id: str) -> TemplateGroup:
    group = db.get(TemplateGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return group


def _erp_field(db: Session, field_id: str) -> tuple[ErpField, ErpWorkbook, TemplateGroup]:
    f = db.get(ErpField, field_id)
    if f is None:
        raise HTTPException(status_code=404, detail="That field no longer exists - reload the page.")
    wb = db.get(ErpWorkbook, f.workbook_id)
    return f, wb, _group(db, wb.group_id)


def _template_docs(db: Session, group_id: str) -> list[TemplateDocument]:
    return (db.query(TemplateDocument).filter(TemplateDocument.group_id == group_id)
            .order_by(TemplateDocument.order_index, TemplateDocument.created_at).all())


def _marks(db: Session, docs: list[TemplateDocument]) -> list[FieldMark]:
    ids = [d.id for d in docs]
    return db.query(FieldMark).filter(FieldMark.document_id.in_(ids)).all() if ids else []


# ---- the ERP Excel mapping (TemplateGroup.excel_config) ------------------------------------------

def _normalised_config(group: TemplateGroup) -> dict:
    """A deep copy of the template's excel_config in the multi-sheet shape. The original
    single-sheet shape is always written one row per line item, so it becomes one sheet with
    scope "line" - exactly what app.core.excel_entry.sheet_plans already treats it as."""
    cfg = copy.deepcopy(group.excel_config or {})
    if not isinstance(cfg.get("sheets"), list) or not cfg["sheets"]:
        if cfg.get("sheet") or cfg.get("columns"):
            cfg["sheets"] = [{"sheet": cfg.get("sheet"), "header_row": int(cfg.get("header_row") or 1),
                              "scope": "line", "columns": cfg.get("columns") or []}]
        else:
            cfg["sheets"] = []
        for k in ("sheet", "header_row"):
            cfg.pop(k, None)
        cfg["columns"] = []
    cfg.setdefault("source", "template")
    return cfg


def _save_config(db: Session, group: TemplateGroup, cfg: dict) -> None:
    group.excel_config = cfg
    flag_modified(group, "excel_config")


def _plan(cfg: dict, sheet: str) -> dict | None:
    return next((p for p in cfg["sheets"] if (p.get("sheet") or "") == sheet), None)


def _column(plan: dict, column: str | None, header: str | None) -> dict | None:
    cols = plan.get("columns") or []
    if column:
        hit = next((c for c in cols if (c.get("column") or "").upper() == column.upper()), None)
        if hit is not None:
            return hit
    if header:
        h = header.strip().lower()
        return next((c for c in cols if (c.get("header") or "").strip().lower() == h), None)
    return None


def _plain_label(col: dict | None) -> str | None:
    """The live field a column reads, when it reads ONE plain field (not a serial, literal,
    placeholder or a pick between fields)."""
    if not col:
        return None
    f = (col.get("field") or "").strip()
    if not f or f.startswith("#") or "[[" in f or col.get("pick_by"):
        return None
    return f


def _labels(db: Session, group: TemplateGroup) -> set[str]:
    docs = _template_docs(db, group.id)
    out = {m.label_name for m in _marks(db, docs)}
    out |= {c.label_name for c in db.query(CustomField).filter(CustomField.group_id == group.id)}
    return out


def _clean_ai_map(val) -> dict:
    """An AI-aided field's setting (see excel_entry.ai_map_value), checked and tidied."""
    import re as _re

    if not isinstance(val, dict):
        raise HTTPException(status_code=422, detail="AI-aided field: expected the list and the instruction.")
    options = []
    for o in val.get("options") or []:
        if not isinstance(o, dict):
            continue
        label, code = str(o.get("label") or "").strip(), str(o.get("code") or "").strip()
        if not label and not code:
            continue
        if not code:
            raise HTTPException(status_code=422, detail=f"AI-aided field: {label!r} has no code.")
        aliases = [str(a).strip() for a in (o.get("aliases") or []) if str(a).strip()]
        options.append({"label": label or code, "code": code, **({"aliases": aliases} if aliases else {})})
    if not options:
        raise HTTPException(status_code=422, detail="AI-aided field: add at least one value and its code.")
    pattern = str(val.get("pattern") or "").strip()
    if pattern:
        try:
            rx = _re.compile(pattern)
        except _re.error as exc:
            raise HTTPException(status_code=422, detail=f"AI-aided field: the code format is not valid ({exc}).")
        bad = [o["code"] for o in options if not rx.fullmatch(o["code"].upper())]
        if bad:
            raise HTTPException(status_code=422, detail=f"AI-aided field: these codes do not fit the code format: {', '.join(bad[:5])}.")
    out = {"options": options, "instruction": str(val.get("instruction") or "").strip()}
    if pattern:
        out["pattern"] = pattern
    ignore = [str(w).strip() for w in (val.get("ignore_words") or []) if str(w).strip()]
    if ignore:
        out["ignore_words"] = ignore
    if val.get("dropdown"):
        out["dropdown"] = True
    return out


def _ai_map_dropdown(db: Session, group: TemplateGroup, col: dict) -> list[str]:
    """"Show as a dropdown": the field the column reads becomes a searchable list of the AI-aided
    field's values that still takes typed text (dropdown_style "search_free"). Returns the
    labels it was set on."""
    spec = col.get("ai_map") or {}
    label = (col.get("field") or "").strip()
    if not spec.get("dropdown") or not label or label.startswith("#") or "[[" in label:
        return []
    # Each value with its code, "Chennai (INMAA1)", so the operator sees what the ERP will get -
    # the saved text carries the code, which ai_map_value reads back as it is.
    choices = [o["label"] if o["label"].strip().upper() == o["code"].strip().upper()
               else f'{o["label"]} ({o["code"]})' for o in spec.get("options") or []]
    done = []
    docs = _template_docs(db, group.id)
    for m in _marks(db, docs):
        if m.label_name == label and (m.width or 0) > 0:
            m.fixed_options, m.dropdown_style = choices, "search_free"
            done.append(label)
    for cf in db.query(CustomField).filter(CustomField.group_id == group.id, CustomField.label_name == label):
        cf.fixed_options, cf.dropdown_style = choices, "search_free"
        done.append(label)
    return done


def _clean_rule(db: Session, group: TemplateGroup, rule: dict) -> dict:
    """Validate one column's rule keys exactly the way excel_entry will read them."""
    out: dict = {}
    labels = _labels(db, group)
    doc_names = {d.name for d in _template_docs(db, group.id)}

    def label_ok(name: str, what: str) -> str:
        name = (name or "").strip()
        if name and name not in labels:
            raise HTTPException(status_code=422, detail=f"{what}: there is no field called {name!r} in this template.")
        return name

    for key, val in rule.items():
        if key not in RULE_KEYS:
            raise HTTPException(status_code=422, detail=f"{key!r} is not a column setting.")
        if val is None or val == "" or val == []:
            out[key] = None  # remove
            continue
        if key == "field":
            v = str(val).strip()
            if v.startswith("#"):
                if v not in ("#row", "#invoice"):
                    raise HTTPException(status_code=422, detail="A serial is #row or #invoice.")
            elif "[[" not in v:
                label_ok(v, "Field")
            out[key] = v
        elif key == "value":
            out[key] = str(val)
        elif key == "values":
            if not isinstance(val, list):
                raise HTTPException(status_code=422, detail="Values per row must be a list.")
            out[key] = ["" if v is None else str(v) for v in val]
        elif key == "pick_by":
            if not isinstance(val, dict) or not str(val.get("field") or "").strip():
                raise HTTPException(status_code=422, detail="Pick by needs the field that chooses.")
            opts = val.get("options") or {}
            if not isinstance(opts, dict) or not opts:
                raise HTTPException(status_code=422, detail="Pick by needs at least one choice -> field.")
            label_ok(val["field"], "Pick by")
            for target in opts.values():
                label_ok(str(target), "Pick by choice")
            default = str(val.get("default") or "").strip()
            if default and default not in opts:
                raise HTTPException(status_code=422, detail="The default choice must be one of the choices.")
            out[key] = {"field": val["field"].strip(), "options": {str(k): str(v) for k, v in opts.items()},
                        **({"default": default} if default else {})}
        elif key == "from_document":
            if str(val) not in doc_names:
                raise HTTPException(status_code=422, detail=f"There is no document called {val!r} in this template.")
            out[key] = str(val)
        elif key == "fallback_fields":
            if not isinstance(val, list):
                raise HTTPException(status_code=422, detail="Fallback fields must be a list.")
            out[key] = [label_ok(str(v), "Fallback") for v in val if str(v).strip()]
        elif key == "ai_map":
            out[key] = _clean_ai_map(val)
        elif key == "transform":
            from app.core.excel_entry import _TRANSFORMS

            if str(val) not in _TRANSFORMS:
                raise HTTPException(status_code=422, detail=f"Unknown transform. Use one of: {', '.join(_TRANSFORMS)}.")
            out[key] = str(val)
        elif key == "max_len":
            try:
                n = int(val)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Max length must be a whole number.") from None
            if n < 1:
                raise HTTPException(status_code=422, detail="Max length must be at least 1.")
            out[key] = n
    return out


class ColumnRuleIn(BaseModel):
    sheet: str
    column: str | None = None   # Excel column letter (template workbook)
    header: str | None = None   # heading text
    rule: dict = Field(default_factory=dict)  # only the keys sent change; null / "" removes a key


@router.get("/groups/{group_id}/excel")
def excel_mapping(group_id: str, db: Session = Depends(get_db),
                  _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    group = _group(db, group_id)
    cfg = _normalised_config(group)
    return {"entry_mode": group.entry_mode, "file_name": cfg.get("file_name"), "sheets": cfg["sheets"],
            "transforms": _transform_names(), "scopes": list(SCOPES),
            "documents": [d.name for d in _template_docs(db, group.id)]}


def _transform_names() -> list[str]:
    from app.core.excel_entry import _TRANSFORMS

    return list(_TRANSFORMS)


@router.patch("/groups/{group_id}/excel-column")
def set_column_rule(group_id: str, body: ColumnRuleIn, db: Session = Depends(get_db),
                    user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Change one ERP Excel column's rule on the live template. A column the mapping does not
    hold yet is added to its sheet (the sheet must already be in the mapping - see
    /excel-sheet)."""
    group = _group(db, group_id)
    cfg = _normalised_config(group)
    plan = _plan(cfg, body.sheet)
    if plan is None:
        raise HTTPException(status_code=409, detail=f"Sheet {body.sheet!r} is not in the ERP Excel mapping yet - add it first.")
    rule = _clean_rule(db, group, body.rule)
    col = _column(plan, body.column, body.header)
    if col is None:
        if not (body.column or body.header):
            raise HTTPException(status_code=422, detail="Say which column (letter or heading).")
        col = {"column": (body.column or "").upper() or None, "header": body.header, "field": ""}
        col = {k: v for k, v in col.items() if v is not None}
        plan.setdefault("columns", []).append(col)
    for key, val in rule.items():
        if val is None:
            col.pop(key, None)
            if key == "field":
                col["field"] = ""
        else:
            col[key] = val
    if col.get("pick_by") and col.get("from_document"):
        raise HTTPException(status_code=422, detail="A column reads one document OR picks between fields, not both.")
    dropdown_on = _ai_map_dropdown(db, group, col)
    _save_config(db, group, cfg)
    db.commit()
    return {"sheet": plan["sheet"], "column": col, "dropdown_on": dropdown_on}


class SheetIn(BaseModel):
    sheet: str
    scope: str | None = None
    header_row: int | None = Field(default=None, ge=1)
    remove: bool = False


@router.patch("/groups/{group_id}/excel-sheet")
def set_sheet(group_id: str, body: SheetIn, db: Session = Depends(get_db),
              user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Add / change / remove one sheet of the live ERP Excel mapping (scope, header row)."""
    group = _group(db, group_id)
    cfg = _normalised_config(group)
    plan = _plan(cfg, body.sheet)
    if body.remove:
        if plan is None:
            raise HTTPException(status_code=404, detail="That sheet is not in the mapping.")
        if len(cfg["sheets"]) == 1:
            raise HTTPException(status_code=409, detail="The mapping needs at least one sheet.")
        cfg["sheets"] = [p for p in cfg["sheets"] if p is not plan]
        _save_config(db, group, cfg)
        db.commit()
        return {"removed": body.sheet}
    if body.scope is not None and body.scope not in SCOPES:
        raise HTTPException(status_code=422, detail=f"Scope is one of: {', '.join(SCOPES)}.")
    if plan is None:
        plan = {"sheet": body.sheet, "header_row": body.header_row or 1, "scope": body.scope or "job", "columns": []}
        cfg["sheets"].append(plan)
    if body.scope is not None:
        plan["scope"] = body.scope
    if body.header_row is not None:
        plan["header_row"] = body.header_row
    _save_config(db, group, cfg)
    db.commit()
    return {"sheet": {k: plan[k] for k in ("sheet", "header_row", "scope")}}


# ---- links: onboarding field -> live field ---------------------------------------------------------

def _special_names() -> list[str]:
    from app.api.v1.screen_designer import PROTECTED_NAMES

    return sorted(PROTECTED_NAMES, key=str.lower)


def _link_rows(db: Session, wb: ErpWorkbook, group: TemplateGroup) -> list[dict]:
    docs = _template_docs(db, group.id)
    marks = _marks(db, docs)
    cfs = db.query(CustomField).filter(CustomField.group_id == group.id).all()
    cfg = _normalised_config(group)
    wb_docs = {d["id"]: d for d in _documents(db, wb)}

    by_label_marks: dict[str, list[FieldMark]] = {}
    for m in marks:
        by_label_marks.setdefault(m.label_name, []).append(m)
    cf_by_label = {c.label_name: c for c in cfs}
    norm: dict[str, str] = {}
    for m in marks:
        for name in (m.label_name, m.display_label):
            if _n(name):
                norm.setdefault(_n(name), m.label_name)
    for c in cfs:
        for name in (c.label_name, c.display_label):
            if _n(name):
                norm.setdefault(_n(name), c.label_name)

    def ref_for(label: str, template_doc_id: str | None) -> tuple[str | None, str | None]:
        ms = [m for m in by_label_marks.get(label, []) if (m.width or 0) > 0] or by_label_marks.get(label, [])
        if ms:
            m = next((m for m in ms if m.document_id == template_doc_id), ms[0])
            return f"m:{m.id}", m.document_id
        if label in cf_by_label:
            return f"c:{cf_by_label[label].id}", None
        return None, None

    rows = []
    fields = (db.query(ErpField).filter(ErpField.workbook_id == wb.id)
              .order_by(ErpField.sheet_name, ErpField.position, ErpField.column_index).all())
    for f in fields:
        doc = wb_docs.get(f.source_document or "")
        tdoc = (doc or {}).get("template_document_id")
        col = None
        if f.sheet_name == ERP_UI_SHEET:
            label, how = f.excel_name, "erp_ui"
        else:
            plan = _plan(cfg, f.sheet_name)
            col = _column(plan, f.column_key, f.excel_name) if plan else None
            label, how = _plain_label(col), "excel"
        ref, ref_doc = ref_for(label, tdoc) if label else (None, None)
        suggested = None
        if not ref:
            guess = norm.get(_n(f.display_label)) or norm.get(_n(f.excel_name))
            if guess:
                suggested = guess
        rows.append({
            "erp_field_id": f.id, "sheet": f.sheet_name, "column": f.column_key, "excel_name": f.excel_name,
            "display_label": f.display_label, "category": f.category, "source_document": f.source_document,
            "template_document_id": tdoc, "label": label if ref else None, "ref": ref, "ref_document_id": ref_doc,
            "how": how if ref else None, "suggested_label": suggested,
            "in_mapping": col is not None or f.sheet_name == ERP_UI_SHEET,
            "column_rule": {k: v for k, v in (col or {}).items() if k not in ("column", "header")} or None,
        })
    return rows


@router.get("/groups/{group_id}/links")
def links(group_id: str, db: Session = Depends(get_db),
          _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """Which live template field each onboarding field fills (or could fill)."""
    group = _group(db, group_id)
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group_id).first()
    labels = _labels(db, group)
    return {
        "workbook_id": wb.id if wb else None,
        "fields": _link_rows(db, wb, group) if wb else [],
        "special_names": [{"name": n, "present": n in labels} for n in _special_names()],
    }


def _link_column(db: Session, f: ErpField, group: TemplateGroup, label: str) -> None:
    """Point the onboarding field's Excel column at `label` (adding the column to its sheet's
    plan when the mapping does not hold it yet). ERP UI fields are linked by name already."""
    if f.sheet_name == ERP_UI_SHEET:
        return
    cfg = _normalised_config(group)
    plan = _plan(cfg, f.sheet_name)
    if plan is None:
        wb = db.get(ErpWorkbook, f.workbook_id)
        sheet = next((s for s in wb.sheets or [] if s.get("name") == f.sheet_name), {})
        plan = {"sheet": f.sheet_name, "header_row": int(sheet.get("header_row") or 1), "scope": "job", "columns": []}
        cfg["sheets"].append(plan)
    col = _column(plan, f.column_key, f.excel_name)
    if col is None:
        col = {"column": (f.column_key or "").upper(), "header": f.excel_name, "field": ""}
        if not col["column"]:
            col.pop("column")
        plan.setdefault("columns", []).append(col)
    col["field"] = label
    col.pop("pick_by", None)
    _save_config(db, group, cfg)


class LinkIn(BaseModel):
    label: str


@router.put("/erp-fields/{field_id}/link")
def link_field(field_id: str, body: LinkIn, db: Session = Depends(get_db),
               user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Link an onboarding field to an existing live field: its ERP Excel column reads it."""
    f, wb, group = _erp_field(db, field_id)
    label = body.label.strip()
    if label not in _labels(db, group) and label not in ("#row", "#invoice"):
        raise HTTPException(status_code=422, detail=f"There is no field called {label!r} in this template.")
    if f.sheet_name == ERP_UI_SHEET and label != f.excel_name:
        raise HTTPException(status_code=409, detail="An ERP UI field is linked by its own name.")
    _link_column(db, f, group, label)
    _history(db, wb, user, "link_template_field", field_id=f.id, detail={"label": label})
    db.commit()
    return {"ok": True}


# ---- create the live field ----------------------------------------------------------------------

def _sample_original(sample: ErpDocSample) -> Path | None:
    folder = Path(sample.stored_dir or "")
    hit = next(iter(sorted(folder.glob("original.*"))), None) if folder.exists() else None
    return hit


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _template_document_for(db: Session, wb: ErpWorkbook, group: TemplateGroup, doc_id: str | None) -> TemplateDocument:
    """The live TemplateDocument behind an onboarding document - created (and recorded on the
    workbook) when the admin added the document in onboarding only."""
    docs = _documents(db, wb)
    doc = next((d for d in docs if d["id"] == doc_id), None)
    if doc is None or doc_id == ADDITIONAL_ID:
        raise HTTPException(status_code=409, detail="Put this field on a document (Step 1) before drawing it.")
    td = db.get(TemplateDocument, doc["template_document_id"]) if doc.get("template_document_id") else None
    if td is None:
        order = len(_template_docs(db, group.id))
        td = TemplateDocument(tenant_id=group.tenant_id, group_id=group.id, name=doc["name"],
                              doc_type="Custom", order_index=order, is_required=False, page_count=0)
        db.add(td)
        db.flush()
        stored = [dict(d, template_document_id=td.id) if d["id"] == doc_id else d for d in (wb.documents or [])]
        wb.documents = stored
        flag_modified(wb, "documents")
    return td


def _ensure_same_sample(db: Session, wb: ErpWorkbook, doc_id: str, td: TemplateDocument) -> str | None:
    """Box coordinates are fractions of the page they were drawn on. If the live document has
    no sample yet, it gets the onboarding sample (so the box sits on the very page it was drawn
    on). If it has a different one, the box is still saved - with a warning to check it."""
    from app.api.v1.template_documents import _render_pages, document_dir

    sample = (db.query(ErpDocSample)
              .filter(ErpDocSample.workbook_id == wb.id, ErpDocSample.doc_id == doc_id).first())
    src = _sample_original(sample) if sample else None
    if src is None:
        raise HTTPException(status_code=409, detail="Upload this document's sample (Step 0) first.")
    if not td.file_path or not Path(td.file_path).exists():
        ddir = document_dir(td.id)
        shutil.rmtree(ddir, ignore_errors=True)
        ddir.mkdir(parents=True, exist_ok=True)
        original = ddir / f"original{src.suffix.lower()}"
        shutil.copyfile(src, original)
        td.page_count = _render_pages(original, ddir / "pages")
        td.file_path = str(original)
        return None
    if _digest(Path(td.file_path)) != _digest(src):
        return ("The live template's sample for this document is a different file from the onboarding "
                "sample, so the box was placed at the same position on the live sample - check it there.")
    return None


class BoxIn(BaseModel):
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0.001, le=1)
    height: float = Field(gt=0.001, le=1)
    label_name: str | None = None
    line_item: bool | None = None


@router.post("/erp-fields/{field_id}/template-box")
def template_box(field_id: str, body: BoxIn, db: Session = Depends(get_db),
                 user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Put a Document Mapping box on the live template: move the live field's box on that
    document, or create the live field there - and link the ERP Excel column to it."""
    from app.api.v1.field_marks import create_mark
    from app.schemas.onboarding import MarkCreate

    f, wb, group = _erp_field(db, field_id)
    if body.x + body.width > 1.0001 or body.y + body.height > 1.0001:
        raise HTTPException(status_code=422, detail="The box runs off the page.")
    td = _template_document_for(db, wb, group, f.source_document)
    warning = _ensure_same_sample(db, wb, f.source_document, td)
    if body.page > max(td.page_count or 1, 1):
        raise HTTPException(status_code=422, detail=f"The live sample has {td.page_count} page(s).")
    current = next((r for r in _link_rows(db, wb, group) if r["erp_field_id"] == f.id), {})
    label = (body.label_name or current.get("label") or f.display_label or f.excel_name or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="Give the field a name.")
    mark = (db.query(FieldMark).filter(FieldMark.document_id == td.id, FieldMark.label_name == label).first())
    created = False
    if mark is not None:
        mark.page_number, mark.x, mark.y, mark.width, mark.height = body.page, body.x, body.y, body.width, body.height
        if body.line_item is not None:
            mark.is_multi_value = body.line_item
        db.commit()
    else:
        db.commit()  # the document / sample changes above
        out = create_mark(document_id=td.id, db=db, _=None, payload=MarkCreate(
            label_name=label, page_number=body.page, x=body.x, y=body.y, width=body.width,
            height=body.height, is_multi_value=bool(body.line_item),
            display_label=f.display_label if f.display_label and f.display_label != label else None))
        mark = db.get(FieldMark, out.id)
        created = True
    _link_column(db, f, group, label)
    _history(db, wb, user, "template_box_created" if created else "template_box_moved", field_id=f.id,
             detail={"label": label, "document": td.name})
    db.commit()
    return {"ref": f"m:{mark.id}", "mark_id": mark.id, "template_document_id": td.id, "label": label,
            "created": created, "warning": warning}


def live_box_for(db: Session, f: ErpField) -> FieldMark | None:
    """The live box (FieldMark) an onboarding Document field fills, or None."""
    wb = db.get(ErpWorkbook, f.workbook_id)
    group = db.get(TemplateGroup, wb.group_id) if wb else None
    if group is None:
        return None
    row = next((r for r in _link_rows(db, wb, group) if r["erp_field_id"] == f.id), None)
    ref = (row or {}).get("ref") or ""
    return db.get(FieldMark, ref[2:]) if ref.startswith("m:") else None


def live_settings_from(f: ErpField, label: str) -> dict:
    """The live custom-field settings an onboarding field's Step 4 configuration stands for -
    used when the live field is created AND every time Step 4 saves (push_to_live), so what is
    configured in onboarding is what jobs use."""
    cfg = f.config or {}
    out: dict = {"ask_operator_required": bool(f.mandatory)}
    # A blank onboarding label never wipes the label already on the live field (2026-11-28) -
    # labels live in the template; onboarding only ever adds or changes one.
    if f.display_label and f.display_label != label:
        out["display_label"] = f.display_label
    if f.category == "fixed":
        out["hardcoded_value"] = cfg.get("value") or f.default_value
        out["hidden_from_capture"] = bool(cfg.get("hidden"))
    elif f.category == "operator":
        out["ask_operator"] = True
        out["ask_operator_hint"] = f.description
        out["hardcoded_value"] = cfg.get("default") or f.default_value
        dropdown = cfg.get("input_type") == "dropdown" and cfg.get("choices")
        out["fixed_options"] = [str(c) for c in cfg["choices"]] if dropdown else None
        out["date_picker"] = cfg.get("input_type") == "date"
    elif f.category == "reference":
        out["is_target_value"] = True
    elif f.category == "ai":
        out["ai_prompt"] = f.ai_prompt
    return out


def push_to_live(db: Session, f: ErpField) -> dict | None:
    """Write an onboarding field's Step 4 configuration onto the live field it fills (one data
    store: jobs read the live template). A box on a document takes only its screen label.
    Nothing happens when the field is not connected to a live field yet. Caller commits."""
    wb = db.get(ErpWorkbook, f.workbook_id)
    group = db.get(TemplateGroup, wb.group_id) if wb else None
    if group is None:
        return None
    row = next((r for r in _link_rows(db, wb, group) if r["erp_field_id"] == f.id), None)
    ref = (row or {}).get("ref") or ""
    if ref.startswith("c:"):
        cf = db.get(CustomField, ref[2:])
        if cf is None:
            return None
        for k, v in live_settings_from(f, cf.label_name).items():
            if k == "ai_prompt" and not v:
                continue            # Step 4 never blanks an AI rule written in the editor
            setattr(cf, k, v)
        return {"ref": ref, "label": cf.label_name}
    if ref.startswith("m:"):
        m = db.get(FieldMark, ref[2:])
        if m is None:
            return None
        # A blank onboarding label leaves the live label as it is (it used to clear it).
        if f.display_label and f.display_label != m.label_name:
            m.display_label = f.display_label
        return {"ref": ref, "label": m.label_name}
    return None


class CustomFieldIn(BaseModel):
    kind: str | None = None  # hardcoded | ai | lookup | composite; default from the category
    label_name: str | None = None


_KIND_FOR = {"fixed": "hardcoded", "operator": "hardcoded", "reference": "hardcoded",
             "ai": "ai", "formula": "composite"}


@router.post("/erp-fields/{field_id}/template-field")
def template_field(field_id: str, body: CustomFieldIn, db: Session = Depends(get_db),
                   user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Create the live custom field an onboarding field needs (Fixed, Operator Input,
    Reference, AI-Derived or Formula), seeded from what onboarding already holds, and link the
    ERP Excel column to it. An existing field of that name is linked instead."""
    from app.api.v1.field_marks import create_custom_field
    from app.schemas.onboarding import CustomFieldCreate

    f, wb, group = _erp_field(db, field_id)
    label = (body.label_name or f.display_label or f.excel_name or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="Give the field a name.")
    existing = (db.query(CustomField).filter(CustomField.group_id == group.id, CustomField.label_name == label).first())
    if existing is None:
        if db.query(FieldMark).join(TemplateDocument, FieldMark.document_id == TemplateDocument.id) \
                .filter(TemplateDocument.group_id == group.id, FieldMark.label_name == label).first():
            raise HTTPException(status_code=409, detail=f"{label!r} is already a field drawn on a document - link to it instead.")
        kind = body.kind or _KIND_FOR.get(f.category or "", "hardcoded")
        seed: dict = {"label_name": label, "kind": kind, **live_settings_from(f, label)}
        if f.category == "reference":
            seed["fuzzy_match"] = True
        elif f.category == "formula":
            seed["per_row"] = True
        out = create_custom_field(group_id=group.id, db=db, _=None,
                                  payload=CustomFieldCreate(**{k: v for k, v in seed.items() if v is not None}))
        cf_id = out.id if hasattr(out, "id") else out["id"]
        created = True
    else:
        cf_id, created = existing.id, False
    _link_column(db, f, group, label)
    _history(db, wb, user, "template_field_created" if created else "template_field_linked", field_id=f.id,
             detail={"label": label})
    db.commit()
    return {"ref": f"c:{cf_id}", "custom_field_id": cf_id, "label": label, "created": created}


# ---- template-level settings with no API before ---------------------------------------------------

class DocumentIn(BaseModel):
    name: str
    doc_type: str = "Custom"
    is_required: bool = True


@router.post("/groups/{group_id}/documents")
def add_document(group_id: str, body: DocumentIn, db: Session = Depends(get_db),
                 user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Add a document to the live template (last in order). The onboarding workbook, when there
    is one, gets it too, so Steps 1 and 2 offer it straight away."""
    group = _group(db, group_id)
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="Give the document a name.")
    docs = _template_docs(db, group.id)
    if any(d.name.lower() == name.lower() for d in docs):
        raise HTTPException(status_code=409, detail=f"The template already has a document called {name!r}.")
    td = TemplateDocument(tenant_id=group.tenant_id, group_id=group.id, name=name, doc_type=body.doc_type or "Custom",
                          order_index=len(docs), is_required=body.is_required, page_count=0)
    db.add(td)
    db.flush()
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group.id).first()
    if wb is not None:
        current = _documents(db, wb)  # seeds + keeps Additional Details last
        listed = [d for d in (wb.documents or []) if d["id"] != ADDITIONAL_ID]
        if not any(d.get("template_document_id") == td.id for d in current):
            extra = [d for d in (wb.documents or []) if d["id"] == ADDITIONAL_ID]
            wb.documents = [*listed, {"id": td.id, "name": td.name, "template_document_id": td.id}, *extra]
            flag_modified(wb, "documents")
    db.commit()
    return {"id": td.id, "name": td.name}


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: Session = Depends(get_db),
                    _: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Remove a document from the live template - only while no job uses it. Its boxes, layout
    and classifier samples go with it; a job's own pages never do (refused instead)."""
    from app.models.job import JobDocument, JobFieldValue

    td = db.get(TemplateDocument, document_id)
    if td is None:
        raise HTTPException(status_code=404, detail="Document not found")
    used = db.query(JobDocument).filter(JobDocument.template_document_id == td.id).count()
    used += db.query(JobFieldValue).filter(JobFieldValue.template_document_id == td.id).count()
    if used:
        raise HTTPException(status_code=409, detail=(
            f"{td.name} is used by existing jobs, so it cannot be deleted (their pages and values would "
            "go with it). Mark it Optional instead."))
    group_id = td.group_id
    wb = db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group_id).first()
    if wb is not None and wb.documents:
        wb.documents = [dict(d, template_document_id=None) if d.get("template_document_id") == td.id else d
                        for d in wb.documents]
        flag_modified(wb, "documents")
    # A custom field that read this document stops pointing at it (display_document_id is
    # cleared by its own ON DELETE SET NULL).
    for cf in db.query(CustomField).filter(CustomField.group_id == group_id).all():
        if td.id in (cf.source_document_ids or []):
            cf.source_document_ids = [x for x in cf.source_document_ids if x != td.id]
            flag_modified(cf, "source_document_ids")
    db.delete(td)
    db.flush()
    for i, d in enumerate(_template_docs(db, group_id)):
        d.order_index = i
    db.commit()
    return {"ok": True}


class OrderIn(BaseModel):
    order_index: int = Field(ge=0)


@router.patch("/documents/{document_id}/order")
def document_order(document_id: str, body: OrderIn, db: Session = Depends(get_db),
                   _: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Move a document to a position; the others close up around it (0, 1, 2 ...)."""
    td = db.get(TemplateDocument, document_id)
    if td is None:
        raise HTTPException(status_code=404, detail="Document not found")
    docs = [d for d in _template_docs(db, td.group_id) if d.id != td.id]
    docs.insert(min(body.order_index, len(docs)), td)
    for i, d in enumerate(docs):
        d.order_index = i
    db.commit()
    return {"order": [d.id for d in docs]}


@router.get("/groups/{group_id}/prompt-history")
def prompt_history(group_id: str, db: Session = Depends(get_db),
                   _: User = Depends(require_role(SUPER_ADMIN))) -> list[dict]:
    from app.models.prompt_change import PromptChange

    _group(db, group_id)
    rows = (db.query(PromptChange).filter(PromptChange.group_id == group_id)
            .order_by(PromptChange.created_at.desc()).limit(300).all())
    return [{"id": r.id, "label_name": r.label_name, "action": r.action, "old_prompt": r.old_prompt,
             "new_prompt": r.new_prompt, "old_format_hint": r.old_format_hint, "new_format_hint": r.new_format_hint,
             "changed_by": r.changed_by_name, "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@router.get("/groups/{group_id}/users")
def template_users(group_id: str, db: Session = Depends(get_db),
                   _: User = Depends(require_role(SUPER_ADMIN))) -> list[dict]:
    """The customer's users and whether each works on this template. A user with NO
    assignments sees every template of the customer (that is how the app reads it)."""
    from app.models.user_template import UserTemplateAssignment

    group = _group(db, group_id)
    users = (db.query(User).filter(User.tenant_id == group.tenant_id).order_by(User.full_name, User.email).all())
    rows = db.query(UserTemplateAssignment).filter(UserTemplateAssignment.tenant_id == group.tenant_id).all()
    mine: dict[str, set[str]] = {}
    for r in rows:
        mine.setdefault(r.user_id, set()).add(r.group_id)
    return [{"id": u.id, "name": u.full_name, "email": u.email, "role": u.role,
             "assigned": group_id in mine.get(u.id, set()), "assignment_count": len(mine.get(u.id, set())),
             "sees_all": not mine.get(u.id)} for u in users]


class AssignIn(BaseModel):
    assigned: bool


@router.put("/groups/{group_id}/users/{user_id}")
def assign_user(group_id: str, user_id: str, body: AssignIn, db: Session = Depends(get_db),
                _: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    from app.models.user import MANAGER
    from app.models.user_template import UserTemplateAssignment

    group = _group(db, group_id)
    u = db.get(User, user_id)
    if u is None or u.tenant_id != group.tenant_id:
        raise HTTPException(status_code=404, detail="User not found for this customer.")
    if u.role == MANAGER:
        raise HTTPException(status_code=400, detail="Manager accounts do not use template assignments.")
    row = (db.query(UserTemplateAssignment)
           .filter(UserTemplateAssignment.user_id == u.id, UserTemplateAssignment.group_id == group.id).first())
    if body.assigned and row is None:
        db.add(UserTemplateAssignment(tenant_id=group.tenant_id, user_id=u.id, group_id=group.id))
    elif not body.assigned and row is not None:
        db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/groups/{group_id}/document-samples")
def document_samples(group_id: str, db: Session = Depends(get_db),
                     _: User = Depends(require_role(SUPER_ADMIN))) -> list[dict]:
    """The page texts the classifier compares new pages with, per document."""
    from app.models.document_sample import DocumentSample

    docs = _template_docs(db, group_id)
    names = {d.id: d.name for d in docs}
    rows = (db.query(DocumentSample).filter(DocumentSample.template_document_id.in_(list(names) or [""]))
            .order_by(DocumentSample.created_at.desc()).all())
    return [{"id": r.id, "template_document_id": r.template_document_id, "document": names.get(r.template_document_id),
             "excerpt": (r.excerpt or "")[:600], "from_job_document": r.job_document_id,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


@router.delete("/document-samples/{sample_id}")
def delete_document_sample(sample_id: str, db: Session = Depends(get_db),
                           _: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    from app.models.document_sample import DocumentSample

    row = db.get(DocumentSample, sample_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---- Step 4: test an AI-Derived field on the template's samples ---------------------------------

@router.post("/custom-fields/{field_id}/test-ai")
def test_ai_field(field_id: str, db: Session = Depends(get_db),
                  _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """Run one AI-Derived field's rule over the text of the template's sample documents (the
    documents it reads, else all of them) and return what it answers on each - nothing is saved.
    The same extraction call a job makes for an AI field."""
    from app.api.v1.field_marks import _document_dir
    from app.core.docai import get_page_ocr
    from app.core.extraction import extract_document_fields

    cf = db.get(CustomField, field_id)
    if cf is None:
        raise HTTPException(status_code=404, detail="Field not found")
    if not (cf.ai_prompt or "").strip():
        raise HTTPException(status_code=422, detail="Write the AI rule first.")
    docs = _template_docs(db, cf.group_id)
    wanted = set(cf.source_document_ids or [])
    if wanted:
        docs = [d for d in docs if d.id in wanted]
    spec = [{"label": cf.label_name, "prompt": cf.ai_prompt, "variations": [], "description": cf.ai_prompt}]
    results = []
    for td in docs:
        if not td.page_count:
            results.append({"document": td.name, "value": None, "note": "no sample uploaded"})
            continue
        parts = []
        for p in range(1, td.page_count + 1):
            try:
                o = get_page_ocr(_document_dir(td.id), p)
                parts.append(o.get("layout_text") or o.get("text") or "")
            except Exception:  # noqa: BLE001 - a page that cannot be read is skipped
                continue
        text = "\n".join(parts)
        if not text.strip():
            results.append({"document": td.name, "value": None, "note": "no text read from the sample"})
            continue
        value = extract_document_fields(text, spec).get(cf.label_name)
        results.append({"document": td.name, "value": value, "note": None})
    return {"field": cf.label_name, "results": results}


# ---- Step 3: generate the reading prompts in bulk ----------------------------------------------

class GeneratePromptsIn(BaseModel):
    mark_ids: list[str] | None = None   # None = every box on the template
    only_missing: bool = True           # False = regenerate even a box that has a prompt


@router.post("/groups/{group_id}/generate-prompts")
def generate_prompts(group_id: str, body: GeneratePromptsIn, db: Session = Depends(get_db),
                     user: User = Depends(require_write_access(SUPER_ADMIN))) -> dict:
    """Build each box's reading profile - label variations, what the value means, its format,
    and the extraction prompt - from its box on the sample (the same generator a new box gets).
    By default only boxes with no prompt yet, so a prompt someone tuned is never overwritten
    unless asked; every overwritten prompt goes into the prompt history."""
    from app.api.v1.field_marks import _apply_profile
    from app.models.prompt_change import PromptChange

    group = _group(db, group_id)
    docs = {d.id: d for d in _template_docs(db, group.id)}
    marks = [m for m in _marks(db, list(docs.values())) if (m.width or 0) > 0]
    if body.mark_ids is not None:
        wanted = set(body.mark_ids)
        marks = [m for m in marks if m.id in wanted]
    done, skipped = [], 0
    for m in marks:
        if body.only_missing and (m.extraction_prompt or "").strip():
            skipped += 1
            continue
        old_prompt, old_hint = m.extraction_prompt, m.value_format_hint
        _apply_profile(m, docs[m.document_id], db)          # commits
        if old_prompt and old_prompt != m.extraction_prompt:
            db.add(PromptChange(tenant_id=group.tenant_id, group_id=group.id, mark_id=m.id,
                                label_name=m.label_name[:100], old_prompt=old_prompt, new_prompt=m.extraction_prompt,
                                old_format_hint=old_hint, new_format_hint=m.value_format_hint, action="regenerate",
                                changed_by_id=user.id,
                                changed_by_name=(user.full_name or user.email or "")[:255] or None))
            db.commit()
        done.append(m.id)
    return {"generated": len(done), "skipped": skipped, "mark_ids": done}


# ---- Step 6: end-to-end test - samples -> extraction -> ERP Excel -> the customer's own Excel ----

def _same(a, b) -> bool:
    x = re.sub(r"[^A-Z0-9.]", "", str(a or "").upper())
    y = re.sub(r"[^A-Z0-9.]", "", str(b or "").upper())
    if not x or not y:
        return False
    if x == y:
        return True
    try:
        return float(x.replace(",", "")) == float(y.replace(",", ""))
    except ValueError:
        return False


@router.post("/groups/{group_id}/end-to-end-test")
def end_to_end_test(group_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """The whole configuration on the template's samples, before going live: every document's
    sample is read with the live prompts, the template's fixed values and formula joins are
    added, the ERP Excel rows are built exactly as a job builds them, and every column is
    compared with the first data row of the customer's own ERP Excel (onboarding Step 0/1).
    Nothing is saved. AI-Derived, reference and operator values are filled on a real job, so
    they are reported as such rather than counted as differences."""
    from app.api.v1.field_marks import demo_extract
    from app.api.v1.jobs import _join_composite_pieces
    from app.core.excel_entry import _rows_for_plan, sheet_plans

    group = _group(db, group_id)
    if (group.entry_mode or "fields") != "excel" or not group.excel_config:
        raise HTTPException(status_code=422, detail="This template has no ERP Excel mapping to test.")
    wb = (db.query(ErpWorkbook).filter(ErpWorkbook.group_id == group.id)
          .order_by(ErpWorkbook.created_at.desc()).first())
    if wb is None:
        raise HTTPException(status_code=422, detail="Upload the customer's ERP Excel in Step 0 first.")

    values: dict[str, str] = {}
    rows: dict[str, list] = {}
    read_from: dict[str, str] = {}
    unread_docs: list[str] = []
    for td in _template_docs(db, group.id):
        if not td.page_count:
            unread_docs.append(td.name)
            continue
        try:
            out = demo_extract(document_id=td.id, db=db, user=user)
        except Exception:  # noqa: BLE001 - one unreadable sample does not stop the test
            unread_docs.append(td.name)
            continue
        for r in out.results:
            if r.extracted_values:
                rows.setdefault(r.label_name, [v or "" for v in r.extracted_values])
                values.setdefault(r.label_name, next((v for v in r.extracted_values if v), "") or "")
            elif r.extracted_value and not values.get(r.label_name):
                values[r.label_name] = r.extracted_value
                values[f"{r.label_name}@@{td.name}"] = r.extracted_value
            read_from.setdefault(r.label_name, td.name)

    filled_on_job: set[str] = set()
    cfs = db.query(CustomField).filter(CustomField.group_id == group.id).all()
    for c in cfs:
        if c.kind == "hardcoded" and c.hardcoded_value and not c.ask_operator and not c.is_target_value:
            values.setdefault(c.label_name, c.hardcoded_value)
        elif c.kind == "ai" or c.ask_operator or c.is_target_value or c.kind == "lookup":
            filled_on_job.add(c.label_name)
    for c in cfs:
        if c.kind == "composite" and c.composite_source_labels:
            pieces = [p.get("fixed", "") if isinstance(p, dict) else str(values.get(p, "") or "")
                      for p in c.composite_source_labels]
            values.setdefault(c.label_name, _join_composite_pieces(pieces, c.composite_separators))

    expected: dict[tuple, str] = {}
    for f in db.query(ErpField).filter(ErpField.workbook_id == wb.id).all():
        sample = str(f.samples[0]).strip() if f.samples and f.samples[0] is not None else ""
        if f.column_key:
            expected[(f.sheet_name, f"col:{f.column_key}")] = sample
        if f.excel_name:
            expected.setdefault((f.sheet_name, f"hdr:{f.excel_name.strip().lower()}"), sample)

    sheets, summary = [], {"match": 0, "differs": 0, "missing": 0, "on_job": 0, "extra": 0}
    for plan in sheet_plans(group.excel_config):
        built = _rows_for_plan(plan, values, rows)
        first = built[0] if built else []
        items = []
        for i, col in enumerate(plan["columns"]):
            exp = expected.get((plan["sheet"], f"col:{col.get('column')}"),
                               expected.get((plan["sheet"], f"hdr:{str(col.get('header') or '').strip().lower()}"), ""))
            got = str(first[i] if i < len(first) and first[i] is not None else "").strip()
            field = col.get("field") or ""
            if not exp and not got:
                continue
            if field in filled_on_job and not got:
                status = "on_job"
            elif not exp:
                status = "extra"
            elif not got:
                status = "missing"
            else:
                status = "match" if _same(exp, got) else "differs"
            summary[status] += 1
            items.append({"column": col.get("column"), "header": col.get("header"), "field": field,
                          "read_from": read_from.get(field), "expected": exp, "built": got, "status": status})
        sheets.append({"sheet": plan["sheet"], "scope": plan["scope"], "rows_built": len(built), "columns": items})
    compared = summary["match"] + summary["differs"] + summary["missing"]
    return {"summary": {**summary, "compared": compared,
                        "match_rate": round(100 * summary["match"] / compared) if compared else None},
            "sheets": sheets, "unread_documents": unread_docs}

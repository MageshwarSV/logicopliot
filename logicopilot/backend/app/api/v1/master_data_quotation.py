"""ERP Master Data - Quotation: one row per ERP quotation name + branch, with its address.

Tenant Admin -> Administration -> Master Data -> ERP Master Data - Quotation. Each row is one
quotation_address reference row (match_value_1 = ERP quotation name, match_value_2 = branch
-> resolved_value = address).

The job screen fills the quotation fields through three tables per template:
  Erp Entry Name     (document quotation name) -> ERP quotation name
  branch_name        (ERP quotation name)      -> branch(es), one row per branch
  quotation_address  (name, branch)            -> address
Adding a record writes all three so it is pickable on a job. Nothing is deleted from here: a
record is made Inactive (hidden from jobs) or Active again, cascading to the branch / name rows
the same way the importer screen does.

Keys are compared case-insensitively: the job screen matches name and branch by upper-casing
both sides, and older rows were stored in mixed case ("Bangalore" vs "BANGALORE"). New rows are
stored upper-cased with single spaces, punctuation kept - exactly what the job screen compares.
"""
import io

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.reference_cache import normalize_key
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN, User

router = APIRouter(prefix="/master-data/quotation-directory", tags=["master data"])

ENTRY_LABEL = "erp entry name"
BRANCH_LABEL = "branch_name"
ADDRESS_LABEL = "quotation_address"


def _key(value: str | None) -> str:
    """Upper case, single spaces - the job screen's own comparison."""
    return " ".join(str(value or "").split()).upper()


def _fields(db: Session, user: User, group_id: str) -> dict[str, CustomField]:
    group = db.get(TemplateGroup, group_id)
    if group is None or group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    out: dict[str, CustomField] = {}
    for cf in db.query(CustomField).filter(CustomField.group_id == group_id).all():
        label = (cf.label_name or "").strip().lower()
        for key, wanted in (("entry", ENTRY_LABEL), ("branch", BRANCH_LABEL), ("address", ADDRESS_LABEL)):
            if label == wanted and key not in out:
                out[key] = cf
    if "address" not in out:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This template has no quotation master data")
    return out


def _row_out(r: Ref) -> dict:
    return {
        "id": r.id, "name": r.match_value_1, "branch": r.match_value_2, "address": r.resolved_value,
        "is_active": r.is_active, "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    found = (
        db.query(CustomField.id, CustomField.group_id, TemplateGroup.name)
        .join(TemplateGroup, TemplateGroup.id == CustomField.group_id)
        .filter(CustomField.tenant_id == user.tenant_id, func.lower(CustomField.label_name) == ADDRESS_LABEL)
        .all()
    )
    templates, seen = [], set()
    for field_id, group_id, group_name in found:
        if group_id in seen:
            continue
        seen.add(group_id)
        rows = db.query(func.count(Ref.id)).filter(Ref.custom_field_id == field_id).scalar() or 0
        templates.append({"group_id": group_id, "name": group_name, "rows": rows})
    templates.sort(key=lambda t: t["name"].lower())
    return {"templates": templates}


@router.get("/{group_id}/rows")
def list_rows(
    group_id: str,
    q: str = Query("", max_length=200),
    sort: str = Query("name", pattern="^(name|branch)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    """Search matches the ERP Quotation Name only (as on the importer screen)."""
    fields = _fields(db, user, group_id)
    query = db.query(Ref).filter(Ref.custom_field_id == fields["address"].id)
    term = q.strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(Ref.match_value_1.ilike(f"%{escaped}%", escape="\\"))
    total = query.count()
    name, branch = func.upper(Ref.match_value_1), func.upper(Ref.match_value_2)
    cols = [name, branch] if sort == "name" else [branch, name]
    order = [c.desc() if direction == "desc" else c.asc() for c in cols] + [Ref.id]
    rows = query.order_by(*order).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "rows": [_row_out(r) for r in rows]}


class NewQuotationRow(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    branch: str = Field(min_length=1, max_length=255)
    address: str = Field(min_length=1)


def _matching(db: Session, field_id: str, name: str, branch: str | None = None, *, branch_in_value: bool = False):
    """Rows of one table for this name (and branch), compared case-insensitively. For the
    branch_name table the branch is the row's VALUE (one row per branch), not a key slot."""
    rows = db.query(Ref).filter(Ref.custom_field_id == field_id, func.upper(Ref.match_value_1) == _key(name)).all()
    rows = [r for r in rows if _key(r.match_value_1) == _key(name)]
    if branch is None:
        return rows
    return [r for r in rows if _key(r.resolved_value if branch_in_value else r.match_value_2) == _key(branch)]


@router.post("/{group_id}/rows", status_code=status.HTTP_201_CREATED)
def add_row(group_id: str, payload: NewQuotationRow, db: Session = Depends(get_db),
            user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    fields = _fields(db, user, group_id)
    name, branch, address = payload.name.strip(), payload.branch.strip(), payload.address.strip()
    if not _key(name) or not _key(branch) or not address:
        raise HTTPException(status_code=422, detail="ERP Quotation Name, Quotation Branch and Address are required.")
    existing = _matching(db, fields["address"].id, name, branch)
    if existing:
        detail = ("This quotation name and branch already exist but are Inactive. Make that record Active instead."
                  if not any(r.is_active for r in existing)
                  else "This quotation name and branch already exist. Edit that record instead.")
        raise HTTPException(status_code=409, detail=detail)
    row = Ref(custom_field_id=fields["address"].id, match_value_1=_key(name), match_value_2=_key(branch),
              match_value_3="", match_value_4="", resolved_value=address)
    db.add(row)
    if "branch" in fields and not _matching(db, fields["branch"].id, name, branch, branch_in_value=True):
        db.add(Ref(custom_field_id=fields["branch"].id, match_value_1=_key(name), match_value_2="",
                   match_value_3="", match_value_4="", resolved_value=branch))
    if "entry" in fields:
        entry_rows = db.query(Ref).filter(Ref.custom_field_id == fields["entry"].id).all()
        if not any(_key(r.resolved_value).strip(" ,.") == _key(name).strip(" ,.") for r in entry_rows):
            db.add(Ref(custom_field_id=fields["entry"].id, match_value_1=normalize_key(name), match_value_2="",
                       match_value_3="", match_value_4="", resolved_value=name))
    db.commit()
    db.refresh(row)
    return _row_out(row)


class QuotationRowUpdate(BaseModel):
    address: str


@router.patch("/{group_id}/rows/{row_id}")
def update_row(group_id: str, row_id: int, payload: QuotationRowUpdate, db: Session = Depends(get_db),
               user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    fields = _fields(db, user, group_id)
    row = db.query(Ref).filter(Ref.id == row_id, Ref.custom_field_id == fields["address"].id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    if not payload.address.strip():
        raise HTTPException(status_code=422, detail="Address cannot be empty.")
    row.resolved_value = payload.address.strip()
    db.commit()
    db.refresh(row)
    return _row_out(row)


class ActivePayload(BaseModel):
    is_active: bool


@router.put("/{group_id}/rows/{row_id}/active")
def set_active(group_id: str, row_id: int, payload: ActivePayload, db: Session = Depends(get_db),
               user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Active / Inactive - never delete. Inactive also switches off the branch row once no
    other active address row has this name + branch, and the name's Erp Entry Name rows once
    no active address row is left for the name. Active switches all of them back on."""
    fields = _fields(db, user, group_id)
    row = db.query(Ref).filter(Ref.id == row_id, Ref.custom_field_id == fields["address"].id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    name, branch = row.match_value_1, row.match_value_2
    row.is_active = payload.is_active
    db.flush()
    still_active = [r for r in _matching(db, fields["address"].id, name) if r.is_active]
    if "branch" in fields and (payload.is_active or not any(_key(r.match_value_2) == _key(branch) for r in still_active)):
        for r in _matching(db, fields["branch"].id, name, branch, branch_in_value=True):
            r.is_active = payload.is_active
    if "entry" in fields and (payload.is_active or not still_active):
        for r in db.query(Ref).filter(Ref.custom_field_id == fields["entry"].id).all():
            if _key(r.resolved_value).strip(" ,.") == _key(name).strip(" ,."):
                r.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return _row_out(row)


@router.get("/{group_id}/export")
def export(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    import openpyxl

    fields = _fields(db, user, group_id)
    group = db.get(TemplateGroup, group_id)
    rows = (db.query(Ref).filter(Ref.custom_field_id == fields["address"].id)
            .order_by(func.upper(Ref.match_value_1), func.upper(Ref.match_value_2)).all())
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Quotations")
    ws.append(["ERP Quotation Name", "Quotation Branch", "Quotation Address", "Status"])
    for r in rows:
        ws.append([r.match_value_1, r.match_value_2, r.resolved_value, "Active" if r.is_active else "Inactive"])
    buffer = io.BytesIO()
    wb.save(buffer)
    stem = "_".join(p for p in "".join(c if c.isalnum() else "_" for c in group.name).split("_") if p)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}_Quotations.xlsx"'},
    )

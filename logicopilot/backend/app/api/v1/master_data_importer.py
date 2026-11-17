"""ERP Master Data - Importer: one row per importer branch, filtered by country.

Tenant Admin -> Administration -> Master Data -> ERP Master Data - Importer. Each row is one
ERP Branch Address reference row (match_value_1 = ERP importer name, match_value_2 = branch,
match_value_3 = country -> resolved_value = address) plus its GSTIN.

The job screen resolves the importer through three tables per template, in a cascade:
  ERP Consignee Name                  (document name, country) -> ERP importer name
  ERP Importer Address / Branch Code  (ERP importer name)      -> branch(es)
  ERP Branch Address                  (name, branch, country)  -> address
Adding a branch here writes all three, so it becomes pickable on a job. Nothing is deleted from
here: a branch is made Inactive (see set_active), which hides it from jobs without losing it.
Editing changes only the address and GSTIN; the key (name, branch, country) is fixed - delete
and add again to change it.
"""
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.reference_cache import normalize_key, stage_upload_row
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN, User

router = APIRouter(prefix="/master-data/importer-directory", tags=["master data"])

NAME_LABEL = "erp consignee name"
BRANCH_LABEL = "erp importer address / branch code"
ADDRESS_LABEL = "erp branch address"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
HEADERS = ["Country", "ERP Importer Name", "Branch", "GSTIN", "Branch Address"]
# Upload columns are found by header name (any order), so the original
# Organization_Branch_Address_Extract_with_GSTIN.xlsx uploads as it is.
HEADER_ALIASES = {
    "country": {"country"},
    "name": {"erp importer name", "importer name", "organization", "organisation", "name", "erp consignee name"},
    "branch": {"branch", "branch name", "branch code"},
    "gstin": {"gstin", "gst", "gst no", "gstin no", "gst number"},
    "address": {"branch address", "address"},
}


def _fields(db: Session, user: User, group_id: str) -> dict[str, CustomField]:
    group = db.get(TemplateGroup, group_id)
    if group is None or group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    out: dict[str, CustomField] = {}
    for cf in db.query(CustomField).filter(CustomField.group_id == group_id).all():
        label = (cf.label_name or "").strip().lower()
        for key, wanted in (("name", NAME_LABEL), ("branch", BRANCH_LABEL), ("address", ADDRESS_LABEL)):
            if label == wanted and key not in out:
                out[key] = cf
    if "address" not in out:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This template has no importer master data")
    return out


def _address_row(db: Session, address_field_id: str, row_id: int) -> Ref:
    row = db.query(Ref).filter(Ref.id == row_id, Ref.custom_field_id == address_field_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    return row


def _clean_gstin(value: str | None) -> str | None:
    v = "".join((value or "").split()).upper()
    if len(v) > 20:
        raise HTTPException(status_code=422, detail="GSTIN is too long (15 characters expected).")
    return v or None


def _row_out(r: Ref) -> dict:
    return {
        "id": r.id, "name": r.match_value_1, "branch": r.match_value_2, "country": r.match_value_3,
        "gstin": r.gstin, "address": r.resolved_value, "is_active": r.is_active,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Templates that have an ERP Branch Address table, each with its countries and counts."""
    found = (
        db.query(CustomField.id, CustomField.group_id, TemplateGroup.name)
        .join(TemplateGroup, TemplateGroup.id == CustomField.group_id)
        .filter(CustomField.tenant_id == user.tenant_id, func.lower(CustomField.label_name) == ADDRESS_LABEL)
        .all()
    )
    templates = []
    seen = set()
    for field_id, group_id, group_name in found:
        if group_id in seen:
            continue
        seen.add(group_id)
        has_gstin = func.sum(case((func.coalesce(Ref.gstin, "") != "", 1), else_=0))
        countries = (
            db.query(Ref.match_value_3, func.count(Ref.id), has_gstin)
            .filter(Ref.custom_field_id == field_id)
            .group_by(Ref.match_value_3)
            .order_by(func.count(Ref.id).desc())
            .all()
        )
        templates.append({
            "group_id": group_id, "name": group_name,
            "countries": [{"country": c, "rows": n, "gstin_rows": int(g or 0)} for c, n, g in countries if c],
        })
    templates.sort(key=lambda t: t["name"].lower())
    return {"templates": templates}


@router.get("/{group_id}/rows")
def list_rows(
    group_id: str,
    country: str = Query(..., min_length=1, max_length=255),
    q: str = Query("", max_length=200),
    gstin_only: bool = Query(False, description="Only branches that have a GSTIN"),
    sort: str = Query("name", pattern="^(name|branch)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    fields = _fields(db, user, group_id)
    query = db.query(Ref).filter(Ref.custom_field_id == fields["address"].id,
                                 Ref.match_value_3 == normalize_key(country))
    if gstin_only:
        query = query.filter(func.coalesce(Ref.gstin, "") != "")
    term = q.strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        query = query.filter(or_(
            Ref.match_value_1.ilike(like, escape="\\"), Ref.match_value_2.ilike(like, escape="\\"),
            Ref.gstin.ilike(like, escape="\\"), Ref.resolved_value.ilike(like, escape="\\"),
        ))
    total = query.count()
    cols = [Ref.match_value_1, Ref.match_value_2] if sort == "name" else [Ref.match_value_2, Ref.match_value_1]
    order = [c.desc() if direction == "desc" else c.asc() for c in cols] + [Ref.id]
    rows = query.order_by(*order).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "rows": [_row_out(r) for r in rows]}


class NewImporterRow(BaseModel):
    country: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    branch: str = Field(min_length=1, max_length=255)
    gstin: str | None = None
    address: str = Field(min_length=1)


def _stage_name_and_branch(db: Session, fields: dict, name: str, branch: str, country: str) -> None:
    """Make a new address row pickable on a job: the name under its country, the branch under
    the name. Only ever adds - an existing row (even with a different spelling) is left alone."""
    if "name" in fields:
        stage_upload_row(db, fields["name"].id, [name, country], name.strip())
    if "branch" in fields:
        stage_upload_row(db, fields["branch"].id, [name, branch], branch.strip())


@router.post("/{group_id}/rows", status_code=status.HTTP_201_CREATED)
def add_row(group_id: str, payload: NewImporterRow, db: Session = Depends(get_db),
            user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    fields = _fields(db, user, group_id)
    values = [payload.name, payload.branch, payload.country]
    if not all(normalize_key(v) for v in values) or not payload.address.strip():
        raise HTTPException(status_code=422, detail="Country, ERP Importer Name, Branch and Address are required.")
    gstin = _clean_gstin(payload.gstin)
    staged = stage_upload_row(db, fields["address"].id, values, payload.address)
    if staged["status"] != "added":
        inactive = not staged["row"].is_active
        db.rollback()
        raise HTTPException(status_code=409, detail=(
            "This importer branch already exists for this country but is Inactive. Make that record Active instead."
            if inactive else "This importer branch already exists for this country. Edit that record instead."))
    staged["row"].gstin = gstin
    _stage_name_and_branch(db, fields, payload.name, payload.branch, payload.country)
    db.commit()
    db.refresh(staged["row"])
    return _row_out(staged["row"])


class ImporterRowUpdate(BaseModel):
    address: str | None = None
    gstin: str | None = None


@router.patch("/{group_id}/rows/{row_id}")
def update_row(group_id: str, row_id: int, payload: ImporterRowUpdate, db: Session = Depends(get_db),
               user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    fields = _fields(db, user, group_id)
    row = _address_row(db, fields["address"].id, row_id)
    if payload.address is not None:
        if not payload.address.strip():
            raise HTTPException(status_code=422, detail="Address cannot be empty.")
        row.resolved_value = payload.address.strip()
    if payload.gstin is not None:
        row.gstin = _clean_gstin(payload.gstin)
    db.commit()
    db.refresh(row)
    return _row_out(row)


class ActivePayload(BaseModel):
    is_active: bool


@router.put("/{group_id}/rows/{row_id}/active")
def set_active(group_id: str, row_id: int, payload: ActivePayload, db: Session = Depends(get_db),
               user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Make an importer branch Active or Inactive - records are never deleted from here.

    Inactive: the address row is no longer offered on jobs; its branch row goes inactive too
    once no other ACTIVE address row needs it (same name + branch), and its name row once no
    active address row is left for that name in that country. Active: all three come back."""
    fields = _fields(db, user, group_id)
    addr_id = fields["address"].id
    row = _address_row(db, addr_id, row_id)
    name, branch, country = row.match_value_1, row.match_value_2, row.match_value_3
    row.is_active = payload.is_active
    db.flush()

    active_addr = db.query(Ref.id).filter(Ref.custom_field_id == addr_id, Ref.match_value_1 == name,
                                          Ref.is_active.is_(True))
    if "branch" in fields:
        branch_rows = db.query(Ref).filter(Ref.custom_field_id == fields["branch"].id, Ref.match_value_1 == name,
                                           Ref.match_value_2 == branch)
        if payload.is_active or active_addr.filter(Ref.match_value_2 == branch).first() is None:
            for r in branch_rows.all():
                r.is_active = payload.is_active
    if "name" in fields:
        if payload.is_active or active_addr.filter(Ref.match_value_3 == country).first() is None:
            for r in db.query(Ref).filter(Ref.custom_field_id == fields["name"].id, Ref.match_value_2 == country).all():
                if r.match_value_1 == name or normalize_key(r.resolved_value) == name:
                    r.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return _row_out(row)


def _column_map(header) -> dict[str, int]:
    found: dict[str, int] = {}
    for i, h in enumerate(header or ()):
        key = " ".join(str(h or "").split()).strip().lower()
        for field, aliases in HEADER_ALIASES.items():
            if key in aliases and field not in found:
                found[field] = i
    missing = [f for f in ("country", "name", "branch", "address") if f not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Row 1 must have the column headings: Country, ERP Importer Name, Branch, GSTIN, Branch Address.",
        )
    return found


@router.post("/{group_id}/upload")
def upload(group_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
           user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Add importer branches from Excel. Only adds: a new (name, branch, country) is added with
    its name and branch rows; an existing one keeps its address, and gets the GSTIN filled in
    if it had none. A different address or GSTIN is reported as a conflict, never overwritten."""
    fields = _fields(db, user, group_id)
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="Upload an .xlsx workbook.")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file exceeds the 15 MB limit.")
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rows = list(wb[wb.sheetnames[0]].iter_rows(values_only=True))
        wb.close()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read that workbook: {exc}") from exc
    if not rows:
        raise HTTPException(status_code=422, detail="That file is empty.")
    cols = _column_map(rows[0])

    addr_id = fields["address"].id
    existing = {
        (r.match_value_1, r.match_value_2, r.match_value_3): r
        for r in db.query(Ref).filter(Ref.custom_field_id == addr_id, Ref.match_value_4 == "").all()
    }
    added = unchanged = gstin_filled = skipped = 0
    conflicts = []

    def cell(row, key):
        i = cols.get(key)
        return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else ""

    for raw in rows[1:]:
        raw = raw or ()
        name, branch, country, address = cell(raw, "name"), cell(raw, "branch"), cell(raw, "country"), cell(raw, "address")
        gstin = "".join(cell(raw, "gstin").split()).upper()[:20] or None
        key = (normalize_key(name), normalize_key(branch), normalize_key(country))
        if not all(key) or not address:
            skipped += 1
            continue
        row = existing.get(key)
        if row is None:
            row = Ref(custom_field_id=addr_id, match_value_1=key[0], match_value_2=key[1],
                      match_value_3=key[2], match_value_4="", resolved_value=address, gstin=gstin)
            db.add(row)
            existing[key] = row
            _stage_name_and_branch(db, fields, name, branch, country)
            db.flush()
            added += 1
            continue
        changed = False
        if gstin and not row.gstin:
            row.gstin = gstin
            gstin_filled += 1
            changed = True
        elif gstin and row.gstin != gstin:
            conflicts.append({"id": row.id, "field": "gstin", "name": key[0], "branch": key[1], "country": key[2],
                              "existing_value": row.gstin, "uploaded_value": gstin})
        if row.resolved_value != address:
            conflicts.append({"id": row.id, "field": "address", "name": key[0], "branch": key[1], "country": key[2],
                              "existing_value": row.resolved_value, "uploaded_value": address})
        elif not changed:
            unchanged += 1
    db.commit()
    return {"rows_added": added, "gstin_filled": gstin_filled, "rows_unchanged": unchanged,
            "rows_skipped": skipped, "conflicts": conflicts[:500], "conflict_count": len(conflicts)}


def _workbook(rows, filename: str) -> Response:
    import openpyxl

    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Importers")
    ws.append(HEADERS)
    for r in rows:
        ws.append([r.match_value_3, r.match_value_1, r.match_value_2, r.gstin or "", r.resolved_value])
    buffer = io.BytesIO()
    wb.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stem(text: str) -> str:
    return "_".join(p for p in "".join(c if c.isalnum() else "_" for c in text).split("_") if p)


@router.get("/{group_id}/template")
def template(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    _fields(db, user, group_id)
    return _workbook([], "ERP_Importer_template.xlsx")


@router.get("/{group_id}/export")
def export(group_id: str, country: str = Query("", max_length=255), gstin_only: bool = Query(False),
           db: Session = Depends(get_db),
           user: User = Depends(require_role(TENANT_ADMIN))):
    """The selected country's importer branches (every country when none is given), optionally
    only those with a GSTIN - the same rows the screen is listing."""
    fields = _fields(db, user, group_id)
    group = db.get(TemplateGroup, group_id)
    query = db.query(Ref).filter(Ref.custom_field_id == fields["address"].id)
    if country.strip():
        query = query.filter(Ref.match_value_3 == normalize_key(country))
    if gstin_only:
        query = query.filter(func.coalesce(Ref.gstin, "") != "")
    rows = query.order_by(Ref.match_value_3, Ref.match_value_1, Ref.match_value_2).yield_per(2000)
    return _workbook(rows, f"{_stem(group.name)}_Importers_{_stem(country) or 'All'}.xlsx")

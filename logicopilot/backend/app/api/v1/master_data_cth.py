"""ERP Master Data - CTH Master: the customer's CTH dump (material master workbook), per template.

Tenant Admin -> Administration -> Master Data -> CTH Master. The workbook is the data - no
database copy - and every job keeps reading it exactly as before. From here a Tenant Admin can
list/search/sort its rows, edit ONE row, add one row, or add a small batch (10-20 rows) from the
3-column template (Part Code, Description, CTH). A full replacement of the dump is never done
from this page; that stays a backend / Super Admin job. See app/core/cth_master_rows.py.
"""
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import cth_master_rows as cth
from app.core import material_master as mm
from app.core.deps import get_db, require_role
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN, User

router = APIRouter(prefix="/master-data/cth-directory", tags=["master data"])

MAX_ADD_FILE_BYTES = 2 * 1024 * 1024
SORT_KEYS = {"part": "part_code", "description": "description", "cth": "cth"}


def _group(db: Session, user: User, group_id: str) -> TemplateGroup:
    group = db.get(TemplateGroup, group_id)
    if group is None or group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return group


def _raise(exc: cth.DumpError):
    raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Templates with a CTH lookup field (or a dump attached), each with its dump's status."""
    groups = db.query(TemplateGroup).filter(TemplateGroup.tenant_id == user.tenant_id).all()
    with_lookup = {
        gid for (gid,) in db.query(CustomField.group_id)
        .filter(CustomField.tenant_id == user.tenant_id, CustomField.kind == "lookup").distinct()
    }
    out = []
    for g in groups:
        attached = mm.master_path(g.id).exists()
        if g.id not in with_lookup and not attached:
            continue
        parsed = cth.cached(g.id) if attached else None
        out.append({"group_id": g.id, "name": g.name, **cth.status(g.id),
                    "rows": len(parsed["rows"]) if parsed else None})
    out.sort(key=lambda t: t["name"].lower())
    return {"templates": out}


@router.get("/{group_id}/rows")
def list_rows(
    group_id: str,
    q: str = Query("", max_length=200),
    sort: str = Query("part", pattern="^(part|description|cth)$"),
    direction: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(TENANT_ADMIN)),
) -> dict:
    """One page of the dump. ready=false while the sheet is still being read (poll again);
    search matches part code, description or CTH."""
    _group(db, user, group_id)
    info = cth.status(group_id)
    if not info["attached"]:
        return {"ready": True, "total": 0, "rows": [], **info}
    parsed = cth.cached(group_id)
    if parsed is None:
        return {"ready": False, "total": 0, "rows": [], **info}
    rows = cth.view(parsed)
    term = " ".join(q.split()).lower()
    if term:
        rows = [r for r in rows if term in r["part_code"].lower() or term in r["description"].lower()
                or term in r["cth_as_written"].lower() or term in r["cth"]]
    key = SORT_KEYS[sort]
    # Blank cells always last, whichever way the column is sorted.
    filled = sorted((r for r in rows if r[key]), key=lambda r: r[key].lower(), reverse=direction == "desc")
    rows = filled + [r for r in rows if not r[key]]
    return {"ready": True, "total": len(rows), **info,
            "rows": rows[(page - 1) * page_size: page * page_size]}


class CthValues(BaseModel):
    part_code: str = ""
    description: str = ""
    cth: str = ""


class CthEdit(BaseModel):
    expected: CthValues
    values: CthValues


@router.put("/{group_id}/rows/{row}")
def edit_row(group_id: str, row: int, payload: CthEdit, db: Session = Depends(get_db),
             user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Edit one row of the dump (written into the workbook)."""
    _group(db, user, group_id)
    try:
        return cth.update_row(group_id, row, payload.expected.model_dump(), payload.values.model_dump())
    except cth.DumpError as exc:
        _raise(exc)


@router.post("/{group_id}/rows")
def add_row(group_id: str, payload: CthValues, db: Session = Depends(get_db),
            user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Add one row to the dump."""
    _group(db, user, group_id)
    try:
        result = cth.add_rows(group_id, [payload.model_dump()])
    except cth.DumpError as exc:
        _raise(exc)
    if not result["added"]:
        raise HTTPException(status_code=409 if "already" in result["skipped"][0]["reason"] else 422,
                            detail=result["skipped"][0]["reason"])
    return result["added_rows"][0]


@router.post("/{group_id}/rows/upload")
def add_rows_from_file(group_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Add a small batch of new rows from the 3-column template. Only ADDS - existing part codes
    are skipped (edit those rows instead); the dump is never replaced from here."""
    _group(db, user, group_id)
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="Upload the filled-in Template (.xlsx).")
    content = file.file.read(MAX_ADD_FILE_BYTES + 1)
    if len(content) > MAX_ADD_FILE_BYTES:
        raise HTTPException(status_code=413, detail="That file is too big for adding rows. A full update of the dump is done by the backend team.")
    try:
        return cth.add_rows(group_id, cth.read_add_file(content))
    except cth.DumpError as exc:
        _raise(exc)


@router.get("/{group_id}/download")
def download_dump(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    """The current dump file, as it is now (including edits made from this page)."""
    _group(db, user, group_id)
    path = mm.master_path(group_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No CTH dump has been uploaded for this template.")
    return FileResponse(path, filename=mm.original_name(group_id) or path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/{group_id}/template")
def download_template(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    """The 3-column template for adding rows: Part Code, Description, CTH."""
    import openpyxl

    _group(db, user, group_id)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Add CTH rows"
    ws.append(cth.TEMPLATE_HEADERS)
    for col, width in zip("ABC", (22, 60, 14)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="CTH_add_rows_template.xlsx"'})

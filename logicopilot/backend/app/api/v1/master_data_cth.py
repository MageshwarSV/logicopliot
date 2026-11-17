"""ERP Master Data - CTH Master: the customer's CTH dump (material master workbook), per template.

Tenant Admin -> Administration -> Master Data -> CTH Master. Read-only listing of the dump's
rows (part code, description, CTH) with search, sort and paging, plus "upload a new dump" for
whenever the customer sends a new one. The workbook stays the single source - no database
copy - and every job keeps reading it exactly as before (see app/core/cth_master_rows.py).
"""
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.core import cth_master_rows as cth
from app.core import material_master as mm
from app.core.deps import get_db, require_role
from app.models.custom_field import CustomField
from app.models.template_group import TemplateGroup
from app.models.user import TENANT_ADMIN, User

router = APIRouter(prefix="/master-data/cth-directory", tags=["master data"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
SORT_COLUMNS = {"part": 0, "description": 1, "cth": 3}


def _group(db: Session, user: User, group_id: str) -> TemplateGroup:
    group = db.get(TemplateGroup, group_id)
    if group is None or group.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return group


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Templates with a CTH lookup field (or a dump already attached), each with its dump's
    status. A template without a dump is listed too, so the first one can be uploaded here."""
    groups = db.query(TemplateGroup).filter(TemplateGroup.tenant_id == user.tenant_id).all()
    with_lookup = {
        gid for (gid,) in db.query(CustomField.group_id)
        .filter(CustomField.tenant_id == user.tenant_id, CustomField.kind == "lookup").distinct()
    }
    out = []
    for g in groups:
        if g.id not in with_lookup and not mm.master_path(g.id).exists():
            continue
        rows = cth.cached_rows(g.id) if mm.master_path(g.id).exists() else None
        out.append({"group_id": g.id, "name": g.name, **cth.status(g.id),
                    "rows": len(rows) if rows is not None else None})
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
    rows = cth.cached_rows(group_id)
    if rows is None:
        return {"ready": False, "total": 0, "rows": [], **info}
    term = " ".join(q.split()).lower()
    if term:
        rows = [r for r in rows if term in r[0].lower() or term in r[1].lower() or term in r[2].lower() or term in r[3]]
    i = SORT_COLUMNS[sort]
    # Blank cells always last, whichever way the column is sorted.
    filled = sorted((r for r in rows if r[i]), key=lambda r: r[i].lower(), reverse=direction == "desc")
    rows = filled + [r for r in rows if not r[i]]
    total = len(rows)
    page_rows = rows[(page - 1) * page_size: page * page_size]
    return {
        "ready": True, "total": total, **info,
        "rows": [{"part_code": r[0], "description": r[1], "cth": r[3] or r[2], "cth_as_written": r[2]} for r in page_rows],
    }


@router.post("/{group_id}/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_dump(group_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                user: User = Depends(require_role(TENANT_ADMIN))) -> dict:
    """Accept a new dump. It is checked in the background and replaces the current one only if
    jobs could read it; until then (and if it is rejected) the current dump stays in use."""
    _group(db, user, group_id)
    name = (file.filename or "").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="Upload an .xlsx or .xlsm workbook (an old .xls cannot be read).")
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="The file exceeds the 30 MB limit.")
    if not content:
        raise HTTPException(status_code=422, detail="That file is empty.")
    cth.accept_upload(group_id, name, content)
    return {"ok": True, "checking_upload": True, "file_name": name}


@router.get("/{group_id}/download")
def download_dump(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    """The current dump file, exactly as it was uploaded."""
    _group(db, user, group_id)
    path = mm.master_path(group_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="No CTH dump has been uploaded for this template.")
    return FileResponse(path, filename=mm.original_name(group_id) or path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/{group_id}/template")
def download_template(group_id: str, db: Session = Depends(get_db), user: User = Depends(require_role(TENANT_ADMIN))):
    """A blank workbook with the headings the dump needs (the current dump's own, if any)."""
    import openpyxl

    _group(db, user, group_id)
    # From the parsed copy only - reading the workbook here could take ~30 s inside a request.
    headers = cth.cached_headers(group_id)
    if not headers:
        headers = ["Material", "Material Description", "Comm./imp. code no."]
    wb = openpyxl.Workbook()
    wb.active.title = "CTH dump"
    wb.active.append(headers)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="CTH_dump_template.xlsx"'})

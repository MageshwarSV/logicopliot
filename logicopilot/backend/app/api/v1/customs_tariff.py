"""Indian Customs Tariff (ICEGATE) - the global CTH master. See app/core/customs_tariff.py.

  GET  /customs-tariff/summary          any signed-in user (public tariff data)
  GET  /customs-tariff/codes            search, any signed-in user (job screens use it later)
  GET  /customs-tariff/codes/{cth}      one code, any signed-in user
  GET  /customs-tariff/download         the current master as Excel - SaaS / Tenant Admin
  GET  /customs-tariff/uploads          upload history - SaaS Admin
  POST /customs-tariff/upload/preview   what a file would change, nothing written - SaaS Admin
  POST /customs-tariff/upload           replace the master with a file - SaaS Admin
  POST /customs-tariff/try              the matching engine on one line - SaaS Admin
  GET  /customs-tariff/dump-check       customer CTH dumps vs the tariff - SaaS Admin
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import customs_tariff as ct
from app.core.deps import get_current_user, get_db, require_role
from app.models.customs_tariff import CustomsTariff, CustomsTariffUpload
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(prefix="/customs-tariff", tags=["customs tariff"])


def _parse(file: UploadFile) -> ct.ParsedFile:
    content = file.file.read(ct.MAX_FILE_BYTES + 1)
    try:
        return ct.parse_file(content, file.filename or "")
    except ct.TariffError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/summary")
def get_summary(db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> dict:
    return ct.summary(db)


@router.get("/codes")
def list_codes(
    q: str = Query("", max_length=200),
    include_inactive: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    return ct.search(db, q=q, include_inactive=include_inactive, page=page, page_size=page_size)


@router.get("/codes/{cth}")
def get_one(cth: str, db: Session = Depends(get_db), _user: User = Depends(get_current_user)) -> dict:
    row = ct.get_code(db, cth)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{cth} is not in the Indian Customs Tariff.")
    return ct.row_out(row)


@router.get("/uploads")
def list_uploads(db: Session = Depends(get_db), _user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    rows = db.query(CustomsTariffUpload).order_by(CustomsTariffUpload.created_at.desc()).limit(50).all()
    return {"uploads": [ct.upload_out(u) for u in rows]}


@router.post("/upload/preview")
def preview(file: UploadFile = File(...), db: Session = Depends(get_db),
            _user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    return ct.preview_upload(db, _parse(file))


@router.post("/upload")
def upload(file: UploadFile = File(...), db: Session = Depends(get_db),
           user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    return ct.apply_upload(db, _parse(file), file.filename or "tariff", user.email)


@router.get("/download")
def download(include_inactive: bool = False, db: Session = Depends(get_db),
             _user: User = Depends(require_role(SUPER_ADMIN, TENANT_ADMIN))):
    """The master as it is now, in the same columns an upload reads - edit it and upload it back."""
    import openpyxl

    query = db.query(CustomsTariff)
    if not include_inactive:
        query = query.filter(CustomsTariff.is_active.is_(True))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CTH"
    headers = ["CTH", "Description", "Rate", "Unit", "Preferential Rate"]
    if include_inactive:
        headers.append("Active")
    ws.append(headers)
    for t in query.order_by(CustomsTariff.cth):
        row = [t.cth, t.description, t.rate, t.unit, t.preferential_rate]
        if include_inactive:
            row.append("Yes" if t.is_active else "No")
        ws.append(row)
    for col, width in zip("ABCDEF", (12, 60, 14, 10, 18, 8)):
        ws.column_dimensions[col].width = width
    # Codes stay text, so Excel keeps the leading zero (01012100).
    for (cell,) in ws.iter_rows(min_row=2, max_col=1):
        cell.number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Indian_Customs_Tariff.xlsx"'})


class TryIn(BaseModel):
    doc_cth: str = Field("", max_length=40)
    part_code: str = Field("", max_length=255)
    description: str = Field("", max_length=2000)


@router.post("/try")
def try_match(payload: TryIn, db: Session = Depends(get_db),
              _user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """The matching engine on one line, for checking it - nothing is remembered."""
    from app.core.customs_tariff_match import suggest_one

    s = suggest_one(db, payload.doc_cth, payload.part_code, payload.description,
                    tenant_id=None, remember=False)
    row = ct.get_code(db, s.cth) if s.cth else None
    return {"cth": s.cth, "how": s.how, "confidence": s.confidence, "note": s.note,
            "code": ct.row_out(row) if row else None}


@router.get("/dump-check")
def dump_check(db: Session = Depends(get_db), _user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """Each customer CTH dump against the tariff: how many of its codes are not in it."""
    from app.core.customs_tariff_match import dump_check as check

    return {"templates": check(db)}

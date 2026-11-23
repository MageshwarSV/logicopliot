"""Super Admin -> Extraction Corrections: which extracted fields operators keep correcting.

Read-only. See app.core.field_edits for how corrections are counted.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core import field_edits
from app.core.deps import get_db, require_role
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/extraction-corrections", tags=["extraction-corrections"])

# Today / Yesterday / This month and picked dates are India calendar days, as the operators
# see them - not UTC days, which would split an Indian working day at 05:30.
IST = ZoneInfo("Asia/Kolkata")


def _window(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """[start of date_from, start of the day after date_to) in IST. Either end may be open."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="The start date is after the end date.")
    since = datetime.combine(date_from, time.min, IST) if date_from else None
    until = datetime.combine(date_to + timedelta(days=1), time.min, IST) if date_to else None
    return since, until


@router.get("/summary")
def corrections_summary(
    min_jobs: int = Query(3, ge=1, le=1000),
    date_from: date | None = None,
    date_to: date | None = None,
    tenant_id: str | None = None,
    group_id: str | None = None,
    include_operator_fields: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    since, until = _window(date_from, date_to)
    return field_edits.summary(db, min_jobs=min_jobs, tenant_id=tenant_id, group_id=group_id,
                               since=since, until=until,
                               include_operator_fields=include_operator_fields)


@router.get("/edits")
def corrections_edits(
    group_id: str,
    label_name: str,
    template_document_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    since, until = _window(date_from, date_to)
    return field_edits.edits(db, group_id=group_id, label_name=label_name,
                             template_document_id=template_document_id or None,
                             tenant_id=tenant_id, since=since, until=until)

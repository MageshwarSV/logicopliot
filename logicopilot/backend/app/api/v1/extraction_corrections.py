"""Super Admin -> Extraction Corrections: which extracted fields operators keep correcting.

Read-only. See app.core.field_edits for how corrections are counted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core import field_edits
from app.core.deps import get_db, require_role
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/extraction-corrections", tags=["extraction-corrections"])


def _since(days: int | None) -> datetime | None:
    return datetime.now(timezone.utc) - timedelta(days=days) if days and days > 0 else None


@router.get("/summary")
def corrections_summary(
    min_jobs: int = Query(3, ge=1, le=1000),
    days: int | None = Query(None, ge=1, le=3650),
    tenant_id: str | None = None,
    group_id: str | None = None,
    include_operator_fields: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    return field_edits.summary(db, min_jobs=min_jobs, tenant_id=tenant_id, group_id=group_id,
                               since=_since(days), include_operator_fields=include_operator_fields)


@router.get("/edits")
def corrections_edits(
    group_id: str,
    label_name: str,
    template_document_id: str | None = None,
    days: int | None = Query(None, ge=1, le=3650),
    tenant_id: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(SUPER_ADMIN)),
) -> dict:
    return field_edits.edits(db, group_id=group_id, label_name=label_name,
                             template_document_id=template_document_id or None,
                             tenant_id=tenant_id, since=_since(days))

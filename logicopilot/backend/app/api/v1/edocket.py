"""The E-Docket page's saved choices for one job (Job.edocket_state). The page saves as the
operator edits; Print pages renders the same saved docket in front of the documents."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import TenantScope, get_db, get_tenant_scope, require_role, require_write_access
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(tags=["e-docket"])

MAX_STATE_BYTES = 200_000


class EDocketState(BaseModel):
    state: dict


@router.get("/jobs/{job_id}/edocket")
def get_edocket(
    job_id: str,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)),
) -> dict:
    from app.api.v1.jobs import _load_job

    job = _load_job(db, job_id, scope, user)
    return job.edocket_state or {"state": None, "saved_at": None, "saved_by": None}


@router.put("/jobs/{job_id}/edocket")
def save_edocket(
    job_id: str,
    body: EDocketState,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_write_access(OPERATOR, SUPER_ADMIN, ADMIN, GK2)),
) -> dict:
    from app.api.v1.jobs import _load_job

    job = _load_job(db, job_id, scope, user)
    if len(json.dumps(body.state)) > MAX_STATE_BYTES:
        raise HTTPException(status_code=400, detail="The e-docket is too large to save.")
    job.edocket_state = {"state": body.state, "saved_at": datetime.now(timezone.utc).isoformat(),
                         "saved_by": user.full_name or user.email}
    db.commit()
    return job.edocket_state

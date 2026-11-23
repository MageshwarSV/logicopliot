"""Super Admin -> API Cost & Usage. Reads the daily records; Retry / backfill re-collects days.
See app.core.api_cost."""
from __future__ import annotations

import re
import threading
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import api_cost
from app.core.deps import get_db, require_role
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/api-cost", tags=["api-cost"])


@router.get("/daily")
def api_cost_daily(month: str, db: Session = Depends(get_db),
                   _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    return api_cost.daily(db, month)


@router.get("/monthly")
def api_cost_monthly(db: Session = Depends(get_db), _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    return api_cost.monthly(db)


@router.get("/day/{day}")
def api_cost_day(day: date, db: Session = Depends(get_db), _: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    return api_cost.day_detail(db, day)


class RunBody(BaseModel):
    date_from: date
    date_to: date | None = None


@router.post("/run")
def api_cost_run(body: RunBody, user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """Retry one day or backfill a range - runs in the background; the screen refreshes."""
    end = body.date_to or body.date_from
    if end < body.date_from:
        raise HTTPException(status_code=400, detail="The start date is after the end date.")
    days = [body.date_from + timedelta(days=i) for i in range((end - body.date_from).days + 1)]
    if len(days) > api_cost.MAX_MANUAL_DAYS:
        raise HTTPException(status_code=400, detail=f"At most {api_cost.MAX_MANUAL_DAYS} days at a time.")
    threading.Thread(target=api_cost.run, args=(days, "manual", user.email), daemon=True,
                     name="api-cost-manual").start()
    return {"started": True, "days": len(days)}

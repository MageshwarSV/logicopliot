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


class LiveBody(BaseModel):
    date_from: date
    date_to: date


@router.post("/live")
def api_cost_live(body: LiveBody, db: Session = Depends(get_db),
                  user: User = Depends(require_role(SUPER_ADMIN))) -> dict:
    """Fetch OpenAI's real cost for these days NOW (Admin API key, same calls as the live tab),
    save them as daily records, and return them with the pages billed. No schedule needed."""
    from app.core.openai_admin import OpenAIAdminAPIError
    from app.core.system_settings import get_openai_admin_key

    if body.date_to < body.date_from:
        raise HTTPException(status_code=400, detail="The start date is after the end date.")
    if (body.date_to - body.date_from).days + 1 > api_cost.MAX_LIVE_DAYS:
        raise HTTPException(status_code=400, detail=f"At most {api_cost.MAX_LIVE_DAYS} days at a time.")
    key = get_openai_admin_key(db)
    if not key:
        raise HTTPException(status_code=409, detail="No OpenAI Admin key is saved (Platform Settings), so real cost cannot be read.")
    try:
        return api_cost.live(db, body.date_from, body.date_to, key, user.email)
    except OpenAIAdminAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach OpenAI: {exc}")

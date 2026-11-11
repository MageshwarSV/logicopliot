"""The notification bell: the two things that happen to a job after it leaves the operator.

Only two kinds, by explicit request - a job reached the ERP, or it failed to. Anything else a
job does is already visible on the screen the operator is looking at; these two are not,
because by then they have moved on to the next job.

Nothing here is stored as a notification. Both kinds are DERIVED, live, from the jobs the user
can already see, using the very same conditions the Jobs list sidebar counts with
(_category_condition). That is what guarantees the bell and the sidebar can never disagree -
and it means the feature works for jobs that already exist, with no backfill.

Only the READ side is stored (NotificationRead), so dismissing is per person: a Gate Keeper
clearing their bell must not clear it for everyone else in the tenant.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import TenantScope, get_db, get_tenant_scope, require_role
from app.models.job import Job
from app.models.notification_read import NotificationRead
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

ROLES = (OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)

KINDS = ("submitted", "failed")

# How far back the bell looks. A notification about something that happened six weeks ago is
# not a notification, it is a report - and the Jobs list already is one. Also what keeps "mark
# all as read" bounded: it can only ever write rows for what is on screen.
WINDOW_DAYS = 30
MAX_ITEMS = 50


class NotificationOut(BaseModel):
    """id is (kind, job) rather than a row id - there is no notification row to have an id."""
    id: str
    kind: str
    job_id: str
    job_reference: str
    # Why it failed, for the "failed" kind - the operator's first question is always this.
    detail: str | None = None
    at: datetime | None = None
    read: bool


class MarkRead(BaseModel):
    ids: list[str] | None = None


def _event_time(job: Job, kind: str) -> datetime | None:
    """When the thing being announced actually happened.

    For a submission that is ai_submitted_at, which is set once and never moved. For a failure
    there is no such column, so updated_at is the closest honest answer - a failure IS the last
    thing that happened to the job.
    """
    if kind == "submitted":
        return job.ai_submitted_at or job.updated_at
    return job.updated_at


def _collect(db: Session, scope: TenantScope, user: User) -> list[tuple[str, Job]]:
    # Imported here rather than at module import time: jobs.py is a large module that imports
    # a great deal of the app, and importing it at the top of this one makes a circular import
    # the moment anything in that chain wants notifications.
    from app.api.v1.jobs import _category_condition, _jobs_visible_query

    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    out: list[tuple[str, Job]] = []
    for kind in KINDS:
        # Exactly what the sidebar counts under "ERP Submitted Jobs" / "ERP Failed Jobs", and
        # narrowed by the same visibility rules as the Jobs list itself - so the bell can never
        # announce a job the person cannot open.
        rows = (
            _jobs_visible_query(db, scope, user)
            .filter(_category_condition(kind))
            .filter(Job.updated_at >= since)
            .order_by(Job.updated_at.desc())
            .limit(MAX_ITEMS)
            .all()
        )
        out.extend((kind, job) for job in rows)
    # Newest first across both kinds, then capped - so a flood of one kind cannot push the
    # other off the list entirely.
    out.sort(key=lambda kj: _event_time(kj[1], kj[0]) or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return out[:MAX_ITEMS]


def _read_keys(db: Session, user: User) -> set[tuple[str, str]]:
    return {
        (r.job_id, r.kind)
        for r in db.query(NotificationRead).filter(NotificationRead.user_id == user.id).all()
    }


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(*ROLES)),
) -> list[NotificationOut]:
    read = _read_keys(db, user)
    items = []
    for kind, job in _collect(db, scope, user):
        items.append(NotificationOut(
            id=f"{kind}:{job.id}",
            kind=kind,
            job_id=job.id,
            job_reference=job.reference,
            detail=(job.erp_reason or None) if kind == "failed" else None,
            at=_event_time(job, kind),
            read=(job.id, kind) in read,
        ))
    return items


def _mark(db: Session, user: User, pairs: list[tuple[str, str]]) -> int:
    """Insert a read mark for each (kind, job), skipping any already there."""
    if not pairs:
        return 0
    already = _read_keys(db, user)
    now = datetime.now(timezone.utc)
    n = 0
    for kind, job_id in pairs:
        if (job_id, kind) in already:
            continue
        db.add(NotificationRead(tenant_id=user.tenant_id, user_id=user.id,
                                job_id=job_id, kind=kind, read_at=now))
        already.add((job_id, kind))
        n += 1
    db.commit()
    return n


@router.post("/read")
def mark_read(
    payload: MarkRead,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(*ROLES)),
) -> dict:
    """Mark the given notifications read. No ids = every one currently on the bell.

    "Mark all as read" deliberately resolves to what the user can SEE right now rather than to
    a blanket timestamp: a job that failed while the panel was open is not something they have
    read, and must still be waiting afterwards.
    """
    if payload.ids:
        pairs = []
        for raw in payload.ids:
            kind, _, job_id = raw.partition(":")
            if kind not in KINDS or not job_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"not a notification id: {raw}")
            pairs.append((kind, job_id))
        # Only ones they can actually see - an id is guessable, and marking something read is
        # still a write against a job.
        visible = {(k, j.id) for k, j in _collect(db, scope, user)}
        pairs = [p for p in pairs if p in visible]
    else:
        pairs = [(kind, job.id) for kind, job in _collect(db, scope, user)]
    return {"marked": _mark(db, user, pairs)}

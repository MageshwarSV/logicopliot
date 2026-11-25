"""The notification bell: the two things that happen to a job after it leaves the operator.

Only two kinds, by explicit request - a job reached the ERP, or it failed to. Anything else a
job does is already visible on the screen the operator is looking at; these two are not,
because by then they have moved on to the next job.

Nothing here is stored as a notification. Both kinds are DERIVED, live, from the jobs the user
can already see, using the very same conditions the Jobs list sidebar counts with
(_category_condition) - and it means the feature works for jobs that already exist, with no
backfill.

Narrowed further to the jobs THIS user submitted to the ERP (see _mine). Being able to see a
job is not the same as it being yours: an operator with a shipment mode sees every job of that
mode, a GK2 sees every job of theirs that reached GK2 - and the bell used to announce all of
it, so everyone got every colleague's ERP results.

Except a Manager and a Tenant Admin (OVERSIGHT_ROLES), by explicit request: watching the
whole tenant's ERP outcomes IS their job, so they keep every result their Jobs list can show.

Only the READ side is stored (NotificationRead), so dismissing is per person: a Gate Keeper
clearing their bell must not clear it for everyone else in the tenant.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import (
    TenantScope,
    get_db,
    get_tenant_scope,
    require_role,
    scoped_query,
)
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.notification_read import NotificationRead
from app.models.user import ADMIN, GK2, MANAGER, OPERATOR, SUPER_ADMIN, TENANT_ADMIN, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

ROLES = (OPERATOR, SUPER_ADMIN, TENANT_ADMIN, ADMIN, GK2, MANAGER)

KINDS = ("submitted", "failed")

# A rerun files a BRAND NEW entry in the ERP, under a brand new job number - the old one is
# not reused. The job's reference follows it (see _adopt_erp_job_no in jobs.py), so without
# being told, the operator who re-ran it goes looking in SOFTLINK for a number that is no
# longer this job's. Live, 2026-11-25: MAA/SI/03999/26-27 was re-run and filed as
# MAA/SI/04008/26-27.
RENUMBERED = "renumbered"

# Every kind a notification id may carry - the two derived from job state, the two zip-password
# ones, and the renumber. Used to validate ids coming back in on /read.
ID_KINDS = (*KINDS, "password", "password_reminder", RENUMBERED)

# Told about every ERP result they can see, not only their own jobs - see the module docstring.
# Super Admin and Admin are deliberately NOT here: they can see every tenant, and a bell of the
# whole platform's results is noise, not a notification.
OVERSIGHT_ROLES = (MANAGER, TENANT_ADMIN)

# How far back the bell looks. A notification about something that happened six weeks ago is
# not a notification, it is a report - and the Jobs list already is one. Also what keeps "mark
# all as read" bounded: it can only ever write rows for what is on screen.
WINDOW_DAYS = 30
MAX_ITEMS = 50
# A job held for a zip password (app/core/zip_passwords.py) is announced to the operator it is
# assigned to (or anyone, while unassigned) - and again once it has waited this long.
PASSWORD_REMINDER_AFTER = timedelta(hours=4)


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


def _event_time(job: Job, kind: str, event: JobEvent | None = None) -> datetime | None:
    """When the thing being announced actually happened.

    For a submission that is ai_submitted_at, which is set once and never moved. For a failure
    there is no such column, so updated_at is the closest honest answer - a failure IS the last
    thing that happened to the job.

    A renumber has a row of its own and so an exact time - which matters more here than for
    the others: a job re-run twice is renumbered twice, and the read mark has to be able to
    tell the second one from the first (see _is_read).
    """
    if kind == RENUMBERED:
        return event.created_at if event is not None else job.updated_at
    if kind == "submitted":
        return job.ai_submitted_at or job.updated_at
    if kind == "password":
        return job.created_at
    if kind == "password_reminder":
        return (_as_utc(job.created_at) or datetime.now(timezone.utc)) + PASSWORD_REMINDER_AFTER
    return job.updated_at


def _mine(user: User):
    """The jobs this person SUBMITTED to the ERP - only they are told how the run ended.

    Was "created it, had it routed to them, or gave it the GK1 or GK2 approval", which put one
    job's result on up to four bells: the person who submitted it saw it, and so did everyone
    else who had touched it. Applied on top of visibility, never instead of it, so the bell
    still cannot announce a job the person cannot open. Not applied to OVERSIGHT_ROLES."""
    return Job.erp_submitted_by_id == user.id


def _renumbers(db: Session, scope: TenantScope, since: datetime,
               visible) -> list[tuple[str, Job, JobEvent]]:
    """Jobs the ERP filed under a NEW number on their most recent run.

    Read back off the job's own history rather than a column of its own: the rename is already
    written there by _adopt_erp_job_no, for the history popup, so there is nothing to add to
    the jobs table and nothing to backfill - renames that already happened show up too.

    The events are fetched first and the jobs filtered to them, not the other way round: a
    renumber is rare, so this is a handful of rows however many jobs the tenant has.
    """
    from app.api.v1.jobs import RENUMBER_NOTE

    events = (
        scoped_query(db, JobEvent, scope)
        .filter(JobEvent.note.like(f"{RENUMBER_NOTE}:%"), JobEvent.created_at >= since)
        .order_by(JobEvent.created_at.desc())
        .limit(MAX_ITEMS * 4)
        .all()
    )
    # Newest first, so the first one seen for a job IS its latest rename. Only that one is
    # announced: a job re-run three times should say what its number is NOW, not list every
    # number it has ever had.
    latest: dict[str, JobEvent] = {}
    for ev in events:
        latest.setdefault(ev.job_id, ev)
    if not latest:
        return []
    jobs = visible.filter(Job.id.in_(list(latest))).limit(MAX_ITEMS).all()
    return [(RENUMBERED, job, latest[job.id]) for job in jobs]


def _collect(db: Session, scope: TenantScope,
             user: User) -> list[tuple[str, Job, JobEvent | None]]:
    # Imported here rather than at module import time: jobs.py is a large module that imports
    # a great deal of the app, and importing it at the top of this one makes a circular import
    # the moment anything in that chain wants notifications.
    from app.api.v1.jobs import _category_condition, _jobs_visible_query

    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    out: list[tuple[str, Job, JobEvent | None]] = []
    for kind in KINDS:
        # Exactly what the sidebar counts under "ERP Submitted Jobs" / "ERP Failed Jobs", and
        # narrowed by the same visibility rules as the Jobs list itself - so the bell can never
        # announce a job the person cannot open.
        query = _jobs_visible_query(db, scope, user)
        if user.role not in OVERSIGHT_ROLES:
            query = query.filter(_mine(user))
        rows = (
            query
            .filter(_category_condition(kind))
            .filter(Job.updated_at >= since)
            .order_by(Job.updated_at.desc())
            .limit(MAX_ITEMS)
            .all()
        )
        out.extend((kind, job, None) for job in rows)
    # Jobs waiting for a zip password - nothing in them can be read until someone enters it.
    from sqlalchemy import or_

    from app.core.zip_passwords import WAITING_FOR_PASSWORD

    held = _jobs_visible_query(db, scope, user).filter(Job.stage_override == WAITING_FOR_PASSWORD,
                                                       Job.created_at >= since)
    if user.role not in OVERSIGHT_ROLES:
        held = held.filter(or_(Job.assigned_operator_id == user.id, Job.assigned_operator_id.is_(None)))
    now = datetime.now(timezone.utc)
    for job in held.order_by(Job.created_at.desc()).limit(MAX_ITEMS).all():
        out.append(("password", job, None))
        if _as_utc(job.created_at) and _as_utc(job.created_at) + PASSWORD_REMINDER_AFTER <= now:
            out.append(("password_reminder", job, None))
    # Jobs the ERP re-filed under a new number. Same visibility as everything else on the
    # bell, and the same "only what this person submitted" narrowing for non-oversight roles.
    renum_visible = _jobs_visible_query(db, scope, user)
    if user.role not in OVERSIGHT_ROLES:
        renum_visible = renum_visible.filter(_mine(user))
    try:
        out.extend(_renumbers(db, scope, since, renum_visible))
    except Exception:  # noqa: BLE001 - a new kind must never take the whole bell down
        logger.exception("could not collect ERP renumber notifications")
    # Newest first across both kinds, then capped - so a flood of one kind cannot push the
    # other off the list entirely.
    out.sort(key=lambda kj: _as_utc(_event_time(kj[1], kj[0], kj[2]))
             or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return out[:MAX_ITEMS]


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite hands timestamps back without a timezone even from a timezone=True column;
    Postgres does not. Both are UTC as written, so compare them as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_marks(db: Session, user: User) -> dict[tuple[str, str], NotificationRead]:
    return {
        (r.job_id, r.kind): r
        for r in db.query(NotificationRead).filter(NotificationRead.user_id == user.id).all()
    }


def _is_read(mark: NotificationRead | None, job: Job, kind: str,
             event: JobEvent | None = None) -> bool:
    """Read means read SINCE it last happened. A job that failed, was read, was retried and
    failed again is a new failure: the read mark is older than this failure, so it is unread
    again - otherwise the second failure would arrive already greyed out, with no pop-up.

    The same holds for a job re-run twice: the second renumber is a second event, with a later
    time, so it comes back unread even though the first one was dismissed."""
    if mark is None:
        return False
    at = _as_utc(_event_time(job, kind, event))
    return at is None or _as_utc(mark.read_at) >= at


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(get_tenant_scope),
    user: User = Depends(require_role(*ROLES)),
) -> list[NotificationOut]:
    marks = _read_marks(db, user)
    items = []
    for kind, job, event in _collect(db, scope, user):
        if kind == "failed":
            detail = job.erp_reason or None
        elif kind == RENUMBERED:
            # The note without its own prefix - the bell's own heading already says what
            # happened, so the detail is just the two numbers.
            detail = (event.note or "").split(":", 1)[-1].strip() or None
        else:
            detail = None
        items.append(NotificationOut(
            id=f"{kind}:{job.id}",
            kind=kind,
            job_id=job.id,
            job_reference=job.reference,
            detail=detail,
            at=_event_time(job, kind, event),
            read=_is_read(marks.get((job.id, kind)), job, kind, event),
        ))
    return items


def _mark(db: Session, user: User,
          items: list[tuple[str, Job, JobEvent | None]]) -> int:
    """Mark each (kind, job) read now, skipping any already read since it last happened.

    One row per person per job per kind (uq_notification_read): a stale mark - from before
    the latest failure - is moved forward rather than a second row added. The tenant comes
    from the JOB, not the user: a Super Admin has no tenant of their own, and the column is
    NOT NULL.
    """
    if not items:
        return 0
    marks = _read_marks(db, user)
    now = datetime.now(timezone.utc)
    n = 0
    for kind, job, event in items:
        mark = marks.get((job.id, kind))
        if _is_read(mark, job, kind, event):
            continue
        if mark is None:
            mark = NotificationRead(tenant_id=job.tenant_id, user_id=user.id,
                                    job_id=job.id, kind=kind, read_at=now)
            db.add(mark)
            marks[(job.id, kind)] = mark
        else:
            mark.read_at = now
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
    on_bell = _collect(db, scope, user)
    if payload.ids:
        wanted = set()
        for raw in payload.ids:
            kind, _, job_id = raw.partition(":")
            if kind not in ID_KINDS or not job_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"not a notification id: {raw}")
            wanted.add((kind, job_id))
        # Only ones they can actually see - an id is guessable, and marking something read is
        # still a write against a job.
        items = [(k, j, e) for k, j, e in on_bell if (k, j.id) in wanted]
    else:
        items = on_bell
    return {"marked": _mark(db, user, items)}

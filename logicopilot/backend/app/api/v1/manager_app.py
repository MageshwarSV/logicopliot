"""The native Manager app's API. Every route here is a GET, and that is the whole design.

A manager watches; they never act. There is deliberately no POST/PATCH/DELETE anywhere in
this file - not "none yet", none. Anything a manager might want done is something to ask a
GK1 or GK2 to do in the portal, which is the same rule the web-side MANAGER role already
follows (see app/models/user.py).

Two things every route here depends on, and neither may be weakened:

TENANT. An app_manager is tenant-scoped by the users table's own CHECK constraint (only
super_admin/admin/saas_finance may have a NULL tenant_id), so `user.tenant_id` is always a
real company here. It is applied to every query below by hand rather than through
scoped_query() only because these are grouped aggregate queries over columns, not
`db.query(Job)` rows - the filter it applies is identical, and _assert_tenant() exists so a
future refactor cannot quietly drop it.

MODE. `assigned_modes` is the login's hard limit, and _resolve_modes() INTERSECTS whatever
the phone asks for with it. The phone's own multi-select is a convenience that narrows what
the server already permits; it can never widen it, so a Sea Import login cannot see Air
Import by sending a different query string.

"Completed" is NOT defined here. It is imported from the jobs API so the app and the portal
can never drift into disagreeing about what finished means - if the Jobs list says 8
completed, this says 8 too, by construction.
"""
from datetime import date as Date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

# The one definition of "this job is finished", shared with the Jobs list. See the module
# docstring. _category_condition is private to that module by name, but it is imported here
# on purpose: a second copy of this rule is exactly the bug worth preventing.
from app.api.v1.jobs import _category_condition
from app.core.deps import get_db, require_role
from app.core.modes import MODES
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import APP_MANAGER, User
from app.schemas.manager_app import (
    AppIdentity,
    DashboardOut,
    ModeCount,
    ReportOut,
    ReportRow,
    TrendOut,
    TrendPoint,
    UsageOut,
    UserCount,
)

router = APIRouter(prefix="/manager-app", tags=["manager-app"])

# India Standard Time, in minutes east of UTC. Only a DEFAULT: completed_at is stored in UTC,
# and "jobs completed on the 15th" means the 15th where the manager is standing, so the app
# sends its own device offset and this is simply what to assume when it does not. India has
# no daylight saving, so a fixed number is correct here in a way it would not be elsewhere.
DEFAULT_TZ_OFFSET_MINUTES = 330

# A report is for reading on a phone, not for bulk export. The cap stops a year-wide range
# from building a response nobody can scroll, and the total above it is always exact.
MAX_REPORT_ROWS = 500

# How many names the dashboard's "top users" shows before the app's own "View All" takes
# over (which calls /usage, where the full ranked list lives).
TOP_USERS_ON_DASHBOARD = 3


def _assert_tenant(user: User) -> str:
    """An app_manager with no tenant should be impossible - the users table's CHECK
    constraint forbids it. Checked anyway, because the cost of being wrong is one company
    seeing another's shipments, and 500 is the right answer to an impossible state."""
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="This app login is not attached to a company.",
        )
    return user.tenant_id


def _resolve_modes(user: User, requested: str | None) -> list[str]:
    """The login's allowed modes, narrowed by what the phone asked for.

    Returns the INTERSECTION, always. An unknown or unpermitted mode in the query string is
    dropped silently rather than erroring, so a phone still holding a preference from before
    its modes were changed keeps working on whatever it is still allowed - it just stops
    seeing what it no longer may. Asking for nothing means everything allowed.
    """
    allowed = [m for m in (user.assigned_modes or []) if m in MODES]
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This app login has no shipment modes assigned. Ask your administrator "
                   "to set which shipment types it should watch.",
        )
    if not requested:
        return sorted(allowed)
    asked = {m.strip() for m in requested.split(",") if m.strip()}
    chosen = sorted(asked & set(allowed))
    if not chosen:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="None of the selected shipment types are available to this login.",
        )
    return chosen


def _day_bounds(day: Date, tz_offset: int) -> tuple[datetime, datetime]:
    """The UTC half-open range [start, end) covering one LOCAL calendar day.

    completed_at is stored in UTC. Comparing it against a bare date would put everything
    before 05:30 IST on the previous day - which is not a rounding error, it is jobs
    appearing under the wrong date on the one screen whose whole job is dates.
    """
    start_local = datetime.combine(day, time.min)
    start_utc = (start_local - timedelta(minutes=tz_offset)).replace(tzinfo=timezone.utc)
    return start_utc, start_utc + timedelta(days=1)


def _range_bounds(start: Date, end: Date, tz_offset: int) -> tuple[datetime, datetime]:
    """Same idea across a span. `end` is INCLUSIVE as the user means it - picking the 1st to
    the 7th includes the 7th - so the exclusive bound is the start of the 8th."""
    start_utc, _ = _day_bounds(start, tz_offset)
    _, end_utc = _day_bounds(end, tz_offset)
    return start_utc, end_utc


def _change_pct(current: int, previous: int) -> float | None:
    """None, not 0 and not -100, when there is nothing to compare against. "Down 100%" and
    "no data last period" look identical on a dashboard and mean very different things."""
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _completed_in(db: Session, tenant_id: str, modes: list[str], start: datetime, end: datetime):
    """Every finished job of these modes, in this window. The single query shape every count
    on every screen is built from, so no screen can define "completed" its own way."""
    return (
        db.query(Job)
        .join(TemplateGroup, Job.group_id == TemplateGroup.id)
        .filter(Job.tenant_id == tenant_id)
        .filter(TemplateGroup.mode.in_(modes))
        .filter(_category_condition("completed"))
        # A finished job with no date cannot be placed on a day at all, so it is excluded
        # rather than guessed at. After the backfill this is only jobs completed before the
        # column existed whose history did not reach back far enough.
        .filter(Job.completed_at.isnot(None))
        .filter(Job.completed_at >= start)
        .filter(Job.completed_at < end)
    )


def _count(db: Session, tenant_id: str, modes: list[str], start: datetime, end: datetime) -> int:
    return _completed_in(db, tenant_id, modes, start, end).count()


def _by_mode(
    db: Session, tenant_id: str, modes: list[str],
    start: datetime, end: datetime,
    prev_start: datetime | None = None, prev_end: datetime | None = None,
) -> list[ModeCount]:
    """One row per mode the login asked for - including modes with nothing in them.

    A mode that scored zero is still shown, deliberately: "Air Import 0" is information, and
    a row quietly missing from a dashboard reads as a bug rather than as a quiet day.
    """
    rows = dict(
        _completed_in(db, tenant_id, modes, start, end)
        .with_entities(TemplateGroup.mode, func.count(Job.id))
        .group_by(TemplateGroup.mode)
        .all()
    )
    previous: dict[str, int] = {}
    if prev_start is not None and prev_end is not None:
        previous = dict(
            _completed_in(db, tenant_id, modes, prev_start, prev_end)
            .with_entities(TemplateGroup.mode, func.count(Job.id))
            .group_by(TemplateGroup.mode)
            .all()
        )
    out: list[ModeCount] = []
    for mode in modes:
        now = int(rows.get(mode, 0))
        if prev_start is None:
            out.append(ModeCount(mode=mode, completed=now))
            continue
        was = int(previous.get(mode, 0))
        out.append(ModeCount(mode=mode, completed=now, previous=was, change_pct=_change_pct(now, was)))
    return out


def _by_user(
    db: Session, tenant_id: str, modes: list[str],
    start: datetime, end: datetime, limit: int | None = None,
) -> list[UserCount]:
    """Who finished them, ranked.

    Credited to the operator the job is ASSIGNED to, falling back to whoever created it: the
    person who did the work, not the person who happened to click the last button. Jobs with
    neither (an email-created job nobody picked up yet) are grouped under one "Unassigned"
    row rather than dropped, so the rows always add up to the total shown above them.
    """
    owner = func.coalesce(Job.assigned_operator_id, Job.created_by_id)
    rows = (
        _completed_in(db, tenant_id, modes, start, end)
        .with_entities(owner.label("owner_id"), func.count(Job.id).label("n"))
        .group_by(owner)
        .order_by(func.count(Job.id).desc())
        .all()
    )
    total = sum(int(r.n) for r in rows) or 1
    ids = [r.owner_id for r in rows if r.owner_id]
    names: dict[str, str] = {}
    if ids:
        names = {
            u.id: (u.full_name or u.email)
            for u in db.query(User).filter(User.id.in_(ids)).all()
        }
    out = [
        UserCount(
            user_id=r.owner_id,
            name=names.get(r.owner_id, "Unassigned") if r.owner_id else "Unassigned",
            completed=int(r.n),
            share_pct=round(int(r.n) / total * 100, 1),
        )
        for r in rows
    ]
    return out[:limit] if limit else out


def _customer_names(db: Session, jobs: list[Job]) -> dict[str, str | None]:
    """The customer on each job, for a whole page of them in two queries rather than two per
    job. Same bulk shape list_jobs uses, and it calls the SAME _customer_name, so a report
    names a customer identically to the Jobs list rather than nearly-identically.

    Worth the two extra queries: at five hundred rows the unprefetched path would be a
    thousand, which is the difference between a report and a timeout.
    """
    if not jobs:
        return {}
    from app.api.v1.jobs import _customer_name
    from app.models.custom_field import CustomField
    from app.models.job import JobFieldValue

    job_ids = [j.id for j in jobs]
    group_ids = list({j.group_id for j in jobs})

    hardcoded_by_group: dict[str, set] = {gid: set() for gid in group_ids}
    for cf in (
        db.query(CustomField.id, CustomField.group_id)
        .filter(CustomField.group_id.in_(group_ids), CustomField.kind == "hardcoded")
        .all()
    ):
        hardcoded_by_group[cf.group_id].add(cf.id)

    fvs_by_job: dict[str, list] = {jid: [] for jid in job_ids}
    for fv in (
        db.query(JobFieldValue)
        .filter(JobFieldValue.job_id.in_(job_ids), JobFieldValue.row_index.is_(None))
        .all()
    ):
        fvs_by_job[fv.job_id].append(fv)

    return {
        job.id: _customer_name(
            db, job,
            rows=fvs_by_job.get(job.id, []),
            hardcoded_ids=hardcoded_by_group.get(job.group_id, set()),
        )
        for job in jobs
    }


@router.get("/me", response_model=AppIdentity)
def whoami(
    db: Session = Depends(get_db),
    user: User = Depends(require_role(APP_MANAGER)),
) -> AppIdentity:
    """What the phone draws its mode picker from. Called on every launch, so the picker
    reflects a change an administrator made without the app needing to be reinstalled."""
    tenant_id = _assert_tenant(user)
    company = None
    if user.tenant is not None:
        company = getattr(user.tenant, "name", None)
    return AppIdentity(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        tenant_id=tenant_id,
        company_name=company,
        allowed_modes=sorted(m for m in (user.assigned_modes or []) if m in MODES),
    )


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    date: Date | None = Query(None, description="Local calendar day; defaults to today."),
    modes: str | None = Query(None, description="Comma-separated; defaults to every allowed mode."),
    tz_offset: int = Query(DEFAULT_TZ_OFFSET_MINUTES, description="Minutes east of UTC."),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(APP_MANAGER)),
) -> DashboardOut:
    """One day: how many finished, how that compares with the day before, split by mode, and
    who did the most."""
    tenant_id = _assert_tenant(user)
    chosen = _resolve_modes(user, modes)
    day = date or (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
    previous_day = day - timedelta(days=1)

    start, end = _day_bounds(day, tz_offset)
    prev_start, prev_end = _day_bounds(previous_day, tz_offset)

    total = _count(db, tenant_id, chosen, start, end)
    prev_total = _count(db, tenant_id, chosen, prev_start, prev_end)

    return DashboardOut(
        date=day,
        completed=total,
        previous_date=previous_day,
        previous_completed=prev_total,
        change_pct=_change_pct(total, prev_total),
        by_mode=_by_mode(db, tenant_id, chosen, start, end, prev_start, prev_end),
        top_users=_by_user(db, tenant_id, chosen, start, end, limit=TOP_USERS_ON_DASHBOARD),
        modes=chosen,
    )


@router.get("/trend", response_model=TrendOut)
def trend(
    days: int = Query(7, ge=1, le=180, description="How many days back, ending today."),
    end: Date | None = Query(None, description="Last day of the range; defaults to today."),
    modes: str | None = Query(None),
    tz_offset: int = Query(DEFAULT_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(APP_MANAGER)),
) -> TrendOut:
    """The chart. One point per local day, including days with nothing.

    Counted in ONE grouped query rather than a query per day: at 180 days that is the
    difference between 1 round trip and 180, on a phone that may be on mobile data.
    """
    tenant_id = _assert_tenant(user)
    chosen = _resolve_modes(user, modes)
    last = end or (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
    first = last - timedelta(days=days - 1)
    start_utc, end_utc = _range_bounds(first, last, tz_offset)

    # ONE query, fetching one column: the finish times in the range, nothing else. The day
    # each belongs to is worked out here rather than in SQL.
    #
    # That is deliberate. Shifting a timestamp into the viewer's day is spelled differently
    # by every database, so doing it in SQL means a branch per dialect - two code paths where
    # production only ever runs one, and the untested one is exactly where a bug would sit.
    # Python has no such ambiguity. It is still a single round trip, and one timestamp column
    # over a bounded date range is a small read whatever the tenant's volume.
    shift = timedelta(minutes=tz_offset)
    counts: dict[str, int] = {}
    for (finished,) in (
        _completed_in(db, tenant_id, chosen, start_utc, end_utc)
        .with_entities(Job.completed_at)
        .all()
    ):
        if finished is None:
            continue
        # Postgres hands these back timezone-aware; normalise so the arithmetic is UTC-based
        # either way rather than depending on how the driver happened to return it.
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        local = finished.astimezone(timezone.utc) + shift
        key = local.date().isoformat()
        counts[key] = counts.get(key, 0) + 1

    points: list[TrendPoint] = []
    cursor = first
    while cursor <= last:
        points.append(TrendPoint(date=cursor, completed=counts.get(cursor.isoformat(), 0)))
        cursor += timedelta(days=1)

    return TrendOut(points=points, total=sum(p.completed for p in points), modes=chosen)


@router.get("/usage", response_model=UsageOut)
def usage(
    start: Date | None = Query(None, description="First day; defaults to the 1st of this month."),
    end: Date | None = Query(None, description="Last day, inclusive; defaults to today."),
    modes: str | None = Query(None),
    tz_offset: int = Query(DEFAULT_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(APP_MANAGER)),
) -> UsageOut:
    """Who completed what over a period, ranked, with the same period before it to compare.

    The comparison period is the SAME LENGTH immediately before, not "last month": comparing
    a 3-day window against a 31-day one is the kind of number that gets quoted in a meeting
    and is simply wrong.
    """
    tenant_id = _assert_tenant(user)
    chosen = _resolve_modes(user, modes)
    today = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
    last = end or today
    first = start or last.replace(day=1)
    if first > last:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The start date is after the end date.",
        )

    span = (last - first).days + 1
    prev_last = first - timedelta(days=1)
    prev_first = prev_last - timedelta(days=span - 1)

    start_utc, end_utc = _range_bounds(first, last, tz_offset)
    prev_start_utc, prev_end_utc = _range_bounds(prev_first, prev_last, tz_offset)

    total = _count(db, tenant_id, chosen, start_utc, end_utc)
    prev_total = _count(db, tenant_id, chosen, prev_start_utc, prev_end_utc)

    return UsageOut(
        start=first,
        end=last,
        completed=total,
        previous_completed=prev_total,
        change_pct=_change_pct(total, prev_total),
        users=_by_user(db, tenant_id, chosen, start_utc, end_utc),
        by_mode=_by_mode(db, tenant_id, chosen, start_utc, end_utc, prev_start_utc, prev_end_utc),
        modes=chosen,
    )


@router.get("/reports", response_model=ReportOut)
def reports(
    start: Date | None = Query(None),
    end: Date | None = Query(None),
    modes: str | None = Query(None),
    tz_offset: int = Query(DEFAULT_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(APP_MANAGER)),
) -> ReportOut:
    """The finished jobs themselves, newest first, for a date range and set of modes.

    `total` is the real count even when the row list is capped (see MAX_REPORT_ROWS), so the
    number a manager reads is never the number that happened to fit.
    """
    tenant_id = _assert_tenant(user)
    chosen = _resolve_modes(user, modes)
    today = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
    last = end or today
    first = start or last.replace(day=1)
    if first > last:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The start date is after the end date.",
        )
    start_utc, end_utc = _range_bounds(first, last, tz_offset)

    base = _completed_in(db, tenant_id, chosen, start_utc, end_utc)
    total = base.count()
    jobs = (
        base.with_entities(Job, TemplateGroup.mode)
        .order_by(Job.completed_at.desc())
        .limit(MAX_REPORT_ROWS)
        .all()
    )

    customers = _customer_names(db, [job for job, _ in jobs])

    owner_ids = {j.assigned_operator_id or j.created_by_id for j, _ in jobs}
    owner_ids.discard(None)
    names: dict[str, str] = {}
    if owner_ids:
        names = {
            u.id: (u.full_name or u.email)
            for u in db.query(User).filter(User.id.in_(owner_ids)).all()
        }

    rows = [
        ReportRow(
            job_id=job.id,
            reference=job.reference,
            mode=mode,
            customer_name=customers.get(job.id),
            operator_name=names.get(job.assigned_operator_id or job.created_by_id or ""),
            completed_at=job.completed_at,
        )
        for job, mode in jobs
    ]
    return ReportOut(start=first, end=last, total=total, rows=rows, modes=chosen)

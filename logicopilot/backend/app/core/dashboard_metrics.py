"""Tenant Admin Executive Dashboard: Data Accuracy, Job Processing Time, and Workforce
Productivity.

Workforce Productivity's numbers (FTE, review hours per employee) only cover jobs that
recorded a real actor+timestamp for GK1's and/or GK2's "Approved and Proceed" press on Data
Validation (Job.validation_approved_at/_by_id, gk2_validation_approved_at/_by_id - added
specifically to make this page honest rather than invented, see those columns' own
docstrings). A job that sailed through Data Validation with no explicit approval at all
contributes nothing - WorkforceMetrics.jobs_with_recorded_review says how many of total_jobs
that actually covers, same spirit as Job Processing Time's own "not every job has this stage"
note.

All three pages are scoped to jobs COMPLETED within the selected window (_completed_jobs_query,
matched on Job.completed_at, same as the billing ledger's own convention) - reverted back to
this (2026-11-10) after a brief stint scoping to every job CREATED in the window, which the
user asked restored to "just like old". Data Accuracy additionally now reports jobs_completed
(renamed "Total Completed Jobs" in the UI) alongside its accuracy percentage.

"Completed" here means Job.status == "completed" alone - an ERP entry that has merely been
SUBMITTED (automated or direct) counts the same as one that has also been VERIFIED afterward,
by explicit request (2026-11-10). This is deliberately looser than the Jobs list's own
"completed" category (app.api.v1.jobs._category_condition), which requires an automated run to
also be verified before it counts - these dashboards don't make that distinction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.user import User
from app.models.job_event import JobEvent
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup

# India Standard Time, in minutes east of UTC - same fixed offset manager_app.py uses, and for
# the same reason (no daylight saving in India, so a constant is correct here).
IST_OFFSET_MINUTES = 330


def day_bounds(day: Date, tz_offset: int = IST_OFFSET_MINUTES) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min)
    start_utc = (start_local - timedelta(minutes=tz_offset)).replace(tzinfo=timezone.utc)
    return start_utc, start_utc + timedelta(days=1)


def range_bounds(start: Date, end: Date, tz_offset: int = IST_OFFSET_MINUTES) -> tuple[datetime, datetime]:
    """end is INCLUSIVE, same convention as manager_app.py's own _range_bounds."""
    start_utc, _ = day_bounds(start, tz_offset)
    _, end_utc = day_bounds(end, tz_offset)
    return start_utc, end_utc


def _completed_jobs_query(db: Session, tenant_id: str, start: datetime, end: datetime, modes: list[str] | None):
    """Submitted OR verified both count here - by explicit request (2026-11-10), unlike the
    Jobs list's own stricter "completed" category (app.api.v1.jobs._category_condition), which
    requires an automated run to also be human-verified before it leaves "ERP Submitted Jobs".
    Job.status is set to "completed" the moment an ERP entry succeeds, automated or direct,
    regardless of whether it has been verified afterward yet - see persist_run_outcome/
    complete_job, and Job.completed_at's own stamping in app/db/job_history.py's before_flush
    hook, which fires at that same moment. So a plain Job.status check already covers both
    paths - no separate check on ai_submitted_at/erp_verified_at is needed here."""
    q = (
        db.query(Job)
        .filter(Job.tenant_id == tenant_id)
        .filter(Job.status == "completed")
        .filter(Job.completed_at.isnot(None))
        .filter(Job.completed_at >= start)
        .filter(Job.completed_at < end)
    )
    if modes:
        q = q.join(TemplateGroup, Job.group_id == TemplateGroup.id).filter(TemplateGroup.mode.in_(modes))
    return q


# ---------------------------------------------------------------------------
# Data Accuracy
# ---------------------------------------------------------------------------

@dataclass
class DocTypeAccuracy:
    document_name: str
    documents: int
    fields_extracted: int
    fields_edited: int
    accuracy_pct: float | None


@dataclass
class TrendPoint:
    day: Date
    accuracy_pct: float | None = None
    avg_total_tat_minutes: float | None = None


@dataclass
class JobAccuracy:
    job_id: str
    reference: str
    mode: str | None
    completed_at: datetime | None
    fields_extracted: int
    fields_edited: int
    accuracy_pct: float | None


@dataclass
class AccuracyMetrics:
    documents_processed: int = 0
    fields_extracted_by_ai: int = 0
    fields_manually_edited: int = 0
    accuracy_pct: float | None = None
    # How many jobs this is computed over - same population as Job Processing Time/Workforce
    # Productivity's own total_jobs now (jobs completed in range), shown beside AI Data
    # Accuracy as "Total Completed Jobs".
    jobs_completed: int = 0
    by_document_type: list[DocTypeAccuracy] = field(default_factory=list)
    trend: list[TrendPoint] = field(default_factory=list)
    # One row per job in the window, newest-completed first - the "See More Details" drill-down
    # table under the aggregate cards, so an admin can see WHICH job(s) a low accuracy number
    # actually came from instead of only the tenant-wide total.
    by_job: list[JobAccuracy] = field(default_factory=list)


def _accuracy_pct(extracted: int, edited: int) -> float | None:
    if extracted == 0:
        return None
    return round((1 - edited / extracted) * 100, 1)


def compute_accuracy_metrics(
    db: Session, tenant_id: str, start: Date, end: Date, modes: list[str] | None = None,
) -> AccuracyMetrics:
    """Only genuine document-read fields count (JobFieldValue.mark_id set, i.e. a FieldMark
    the AI actually read off a page) - a custom field's lookup/hardcoded/composite value was
    never an AI "reading" to begin with, so correcting one is not an AI mistake. A FieldMark
    the operator must always type by hand (ask_operator=True, no document source at all) is
    excluded the same way - the AI never attempted those, so they cannot count against it.

    Scoped to jobs COMPLETED in this window (_completed_jobs_query), same population as Job
    Processing Time/Workforce Productivity.
    """
    start_utc, end_utc = range_bounds(start, end)
    jobs_q = _completed_jobs_query(db, tenant_id, start_utc, end_utc, modes)
    job_ids = [r.id for r in jobs_q.with_entities(Job.id).all()]

    out = AccuracyMetrics()
    out.jobs_completed = len(job_ids)
    out.documents_processed = (
        db.query(JobDocument)
        .filter(JobDocument.job_id.in_(job_ids), JobDocument.file_path.isnot(None))
        .count() if job_ids else 0
    )
    if not job_ids:
        return out

    rows = (
        db.query(
            JobFieldValue.job_id,
            JobFieldValue.template_document_id,
            JobFieldValue.extracted_value,
            JobFieldValue.corrected_value,
        )
        .join(FieldMark, JobFieldValue.mark_id == FieldMark.id)
        .filter(JobFieldValue.job_id.in_(job_ids))
        .filter(JobFieldValue.mark_id.isnot(None))
        .filter(JobFieldValue.extracted_value.isnot(None))
        .filter(FieldMark.ask_operator.is_(False))
        .all()
    )

    # Same rows, grouped by job instead of by document type - the per-job drill-down table.
    totals_by_job: dict[str, list[int]] = {}
    for r in rows:
        bucket = totals_by_job.setdefault(r.job_id, [0, 0])
        bucket[0] += 1
        if r.corrected_value is not None and r.corrected_value != r.extracted_value:
            bucket[1] += 1
    jobs_by_id = {
        job.id: (job, mode)
        for job, mode in (
            db.query(Job, TemplateGroup.mode)
            .join(TemplateGroup, Job.group_id == TemplateGroup.id)
            .filter(Job.id.in_(job_ids))
            .all()
        )
    } if job_ids else {}
    out.by_job = sorted(
        (
            JobAccuracy(
                job_id=job_id, reference=job.reference, mode=mode,
                completed_at=job.completed_at,
                fields_extracted=totals_by_job.get(job_id, [0, 0])[0],
                fields_edited=totals_by_job.get(job_id, [0, 0])[1],
                accuracy_pct=_accuracy_pct(*totals_by_job.get(job_id, [0, 0])),
            )
            for job_id, (job, mode) in jobs_by_id.items()
        ),
        key=lambda r: r.completed_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    doc_ids = {r.template_document_id for r in rows if r.template_document_id}
    doc_names = {
        d.id: d.name for d in db.query(TemplateDocument).filter(TemplateDocument.id.in_(doc_ids)).all()
    } if doc_ids else {}
    doc_count_by_id: dict[str, set[str]] = {}

    totals_by_doc: dict[str | None, list[int]] = {}  # doc_id -> [extracted, edited]
    for r in rows:
        key = r.template_document_id
        bucket = totals_by_doc.setdefault(key, [0, 0])
        bucket[0] += 1
        edited = r.corrected_value is not None and r.corrected_value != r.extracted_value
        if edited:
            bucket[1] += 1
        if key:
            doc_count_by_id.setdefault(key, set()).add(r.job_id)

    out.fields_extracted_by_ai = sum(b[0] for b in totals_by_doc.values())
    out.fields_manually_edited = sum(b[1] for b in totals_by_doc.values())
    out.accuracy_pct = _accuracy_pct(out.fields_extracted_by_ai, out.fields_manually_edited)

    for doc_id, (extracted, edited) in sorted(
        totals_by_doc.items(), key=lambda kv: doc_names.get(kv[0], "Additional Details"),
    ):
        if doc_id is None:
            continue  # a mark with no document_id would be a data bug, not a real row to show
        out.by_document_type.append(
            DocTypeAccuracy(
                document_name=doc_names.get(doc_id, "Unknown document"),
                documents=len(doc_count_by_id.get(doc_id, ())),
                fields_extracted=extracted,
                fields_edited=edited,
                accuracy_pct=_accuracy_pct(extracted, edited),
            )
        )

    # Daily trend: re-run the same aggregate per day in range rather than per job, so a job
    # whose completion date falls outside a given day never leaks into that day's bucket.
    cursor = start
    while cursor <= end:
        d_start, d_end = day_bounds(cursor)
        day_job_ids = [
            r.id for r in _completed_jobs_query(db, tenant_id, d_start, d_end, modes)
            .with_entities(Job.id).all()
        ]
        if day_job_ids:
            day_rows = (
                db.query(JobFieldValue.extracted_value, JobFieldValue.corrected_value)
                .join(FieldMark, JobFieldValue.mark_id == FieldMark.id)
                .filter(JobFieldValue.job_id.in_(day_job_ids))
                .filter(JobFieldValue.mark_id.isnot(None))
                .filter(JobFieldValue.extracted_value.isnot(None))
                .filter(FieldMark.ask_operator.is_(False))
                .all()
            )
            extracted = len(day_rows)
            edited = sum(1 for r in day_rows if r.corrected_value is not None and r.corrected_value != r.extracted_value)
            out.trend.append(TrendPoint(day=cursor, accuracy_pct=_accuracy_pct(extracted, edited)))
        else:
            out.trend.append(TrendPoint(day=cursor, accuracy_pct=None))
        cursor += timedelta(days=1)

    return out


# ---------------------------------------------------------------------------
# Job Processing Time
# ---------------------------------------------------------------------------

@dataclass
class JobTiming:
    """One completed job's own extraction/review/ERP-entry durations - the "See More Details"
    drill-down row shared by Job Processing Time and Workforce Productivity (same three stages
    either page's aggregate cards already average), so a slow tenant-wide number can be traced
    to the specific job(s) behind it."""
    job_id: str
    reference: str
    mode: str | None
    created_at: datetime | None
    completed_at: datetime | None
    extraction_minutes: float | None
    review_minutes: float | None
    erp_submission_minutes: float | None


def _group_modes(db: Session, jobs: list[Job]) -> dict[str, str | None]:
    """group_id -> mode for exactly the groups these jobs use - one query, not one per job."""
    group_ids = {j.group_id for j in jobs}
    if not group_ids:
        return {}
    return {
        g.id: g.mode
        for g in db.query(TemplateGroup).filter(TemplateGroup.id.in_(group_ids)).all()
    }


def _job_timings(db: Session, jobs: list[Job]) -> list[JobTiming]:
    modes = _group_modes(db, jobs)
    rows = [
        JobTiming(
            job_id=j.id, reference=j.reference, mode=modes.get(j.group_id),
            created_at=j.created_at, completed_at=j.completed_at,
            extraction_minutes=ext, review_minutes=rev, erp_submission_minutes=erp,
        )
        for j in jobs
        for ext, rev, erp in [_job_stage_durations(j)]
    ]
    rows.sort(key=lambda r: r.completed_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows


@dataclass
class ProcessingTimeMetrics:
    total_jobs: int = 0
    avg_total_tat_minutes: float | None = None
    avg_extraction_minutes: float | None = None
    avg_review_minutes: float | None = None
    avg_erp_submission_minutes: float | None = None
    trend: list[TrendPoint] = field(default_factory=list)
    by_job: list[JobTiming] = field(default_factory=list)


def _extraction_end(job: Job) -> datetime | None:
    """The timestamp right after the LAST "extracting" JobEvent - i.e. when this job's final
    extraction pass actually finished. Shared by _job_stage_durations (Job Processing Time)
    and compute_workforce_metrics (review-time-per-employee), so the two pages can never
    disagree about where extraction ends and review begins."""
    events = sorted(job.events, key=lambda e: e.created_at)
    extraction_end = None
    for i, ev in enumerate(events):
        if ev.status == "extracting":
            extraction_end = events[i + 1].created_at if i + 1 < len(events) else None
    return extraction_end


def _job_stage_durations(job: Job) -> tuple[float | None, float | None, float | None]:
    """(extraction_minutes, review_minutes, erp_submission_minutes) for ONE completed job,
    reconstructed from JobEvent history plus the job's own checkpoint timestamps.

    Extraction: the LAST "extracting" JobEvent's own timestamp, to the next event after it
    (whatever it is) - covers a job re-extracted more than once, using its final pass.
    Review: that extraction end, to ai_submitted_at (the AI/GK2 path's hand-off point) - or,
    for a job submitted directly (ai_submitted_at never set), to completed_at itself, since a
    direct Submit Entry has no separate "review" vs "ERP entry" split; one human action did
    both in real time.
    ERP submission: the LAST submission (erp_submitted_at) to completed_at. A job submitted
    more than once - failed, retried, finished days later - is timed on its final attempt
    only; see _erp_entry_minutes.
    """
    events = sorted(job.events, key=lambda e: e.created_at)
    extraction_start = None
    extraction_end = _extraction_end(job)
    for ev in events:
        if ev.status == "extracting":
            extraction_start = ev.created_at
    extraction_minutes = None
    if extraction_start and extraction_end:
        extraction_minutes = (extraction_end - extraction_start).total_seconds() / 60

    review_end = job.ai_submitted_at or job.completed_at
    review_minutes = None
    if extraction_end and review_end and review_end > extraction_end:
        review_minutes = (review_end - extraction_end).total_seconds() / 60

    return extraction_minutes, review_minutes, _erp_entry_minutes(job, events)


# A job that went through any of these AFTER being submitted was submitted again before it
# finally completed: failed and retried, flagged duplicate, re-extracted, or completed and then
# reopened.
_RESUBMIT_SIGNS = {"failed", "duplicate", "extracting", "completed"}


def _erp_entry_minutes(job: Job, events: list) -> float | None:
    """Last ERP submission -> completion, in minutes.

    erp_submitted_at is overwritten on every submission, so it is the last one. Jobs completed
    before that column existed only have ai_submitted_at, which is the FIRST submission. That
    is still correct for a job submitted exactly once, but for one submitted again later it
    measured the whole gap (502h / 624h on production). Such a job's last submission time was
    never recorded anywhere, so it gets None ("—") rather than a made-up number.
    """
    end = job.completed_at
    if end is None:
        return None
    start = job.erp_submitted_at
    if start is None:
        start = job.ai_submitted_at
        if start is None:
            return None
        # Anything between that first submission and the final completion (with a second's
        # slack either side, since both ends are stamped in the same save as their own events)
        # means it was submitted again later.
        lo, hi = start + timedelta(seconds=1), end - timedelta(seconds=1)
        if any(lo < ev.created_at < hi and ev.status in _RESUBMIT_SIGNS for ev in events):
            return None
    if end < start:
        return None
    return (end - start).total_seconds() / 60


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _sum_of_averages(*averages: float | None) -> float | None:
    """Avg. Total TAT = Avg. Extraction + Avg. Review + Avg. ERP Submission (by request,
    2026-11-11) - NOT each job's own (completed_at - created_at). That span counted every hour
    a job spent idle between re-extraction rounds or waiting for a reviewer as "processing
    time", which could dwarf the three stage averages it sat beside (a single job re-extracted
    three times over 11 days inflated a 3-job average to 97 hours, next to stage averages of
    a few hours each) - confusing next to numbers that only ever measure a job's LAST pass.
    None only when every stage average is itself None (no data at all); a stage with no data
    contributes 0 to the sum rather than making the whole total None."""
    present = [a for a in averages if a is not None]
    return round(sum(present), 1) if present else None


def compute_processing_time_metrics(
    db: Session, tenant_id: str, start: Date, end: Date, modes: list[str] | None = None,
) -> ProcessingTimeMetrics:
    """Scoped to jobs COMPLETED in this window (_completed_jobs_query), same population as Data
    Accuracy/Workforce Productivity. avg_total_tat_minutes is the SUM of the three stage
    averages (see _sum_of_averages), not each job's own creation-to-completion span. The three
    stage averages don't require every stage to be present - _job_stage_durations already
    returns None for a stage a job hasn't reached, which _avg() silently skips.
    """
    start_utc, end_utc = range_bounds(start, end)
    jobs = _completed_jobs_query(db, tenant_id, start_utc, end_utc, modes).all()

    out = ProcessingTimeMetrics(total_jobs=len(jobs))
    if not jobs:
        out.trend = [TrendPoint(day=start + timedelta(days=i)) for i in range((end - start).days + 1)]
        return out

    # One pass per job, kept per-day too (by_day) so the trend chart uses the identical
    # definition as the headline figure, not a different one that happens to share its label.
    by_day: dict[Date, list[tuple[float | None, float | None, float | None]]] = {}
    extractions, reviews, erps = [], [], []
    for j in jobs:
        ext, rev, erp = _job_stage_durations(j)
        if ext is not None:
            extractions.append(ext)
        if rev is not None:
            reviews.append(rev)
        if erp is not None:
            erps.append(erp)
        local_day = (j.completed_at + timedelta(minutes=IST_OFFSET_MINUTES)).date()
        by_day.setdefault(local_day, []).append((ext, rev, erp))

    out.avg_extraction_minutes = _avg(extractions)
    out.avg_review_minutes = _avg(reviews)
    out.avg_erp_submission_minutes = _avg(erps)
    out.avg_total_tat_minutes = _sum_of_averages(
        out.avg_extraction_minutes, out.avg_review_minutes, out.avg_erp_submission_minutes,
    )
    out.by_job = _job_timings(db, jobs)

    # Bucketed by COMPLETED day, matching total_jobs' own scoping.
    cursor = start
    while cursor <= end:
        day_rows = by_day.get(cursor, [])
        day_ext = _avg([r[0] for r in day_rows if r[0] is not None])
        day_rev = _avg([r[1] for r in day_rows if r[1] is not None])
        day_erp = _avg([r[2] for r in day_rows if r[2] is not None])
        out.trend.append(TrendPoint(day=cursor, avg_total_tat_minutes=_sum_of_averages(day_ext, day_rev, day_erp)))
        cursor += timedelta(days=1)

    return out


# ---------------------------------------------------------------------------
# Workforce Productivity
# ---------------------------------------------------------------------------

# Matches the "5 hrs/day x 6 days/week" convention the Executive Dashboard mockup itself
# specifies for its own FTE calculation - Sunday is the one day off; every other day in the
# range counts as a working day, regardless of how that day's own jobs actually landed.
WORKING_HOURS_PER_DAY = 5.0
SUNDAY = 6  # datetime.weekday()'s own numbering: Monday=0 .. Sunday=6


def _working_hours_in_range(start: Date, end: Date) -> float:
    days = (end - start).days + 1
    sundays = sum(1 for i in range(days) if (start + timedelta(days=i)).weekday() == SUNDAY)
    return (days - sundays) * WORKING_HOURS_PER_DAY


@dataclass
class EmployeeReviewRow:
    user_id: str
    name: str
    jobs_reviewed: int
    review_hours: float
    avg_minutes_per_job: float | None


@dataclass
class WorkforceMetrics:
    employee_requirement_fte: float | None = None
    avg_review_minutes_per_job: float | None = None
    total_jobs: int = 0
    human_review_hours: float = 0.0
    by_employee: list[EmployeeReviewRow] = field(default_factory=list)
    # How many of total_jobs actually had a usable GK1 or GK2 approval timestamp to measure -
    # shown on the page so a low number here explains a small human_review_hours figure, same
    # spirit as Job Processing Time's own "not every job has this stage" note.
    jobs_with_recorded_review: int = 0
    # Same per-job extraction/review/ERP-entry breakdown as Job Processing Time's own by_job -
    # shared JobTiming shape, by request, so both "See More Details" pages show the identical
    # three stage times per job.
    by_job: list[JobTiming] = field(default_factory=list)


def compute_workforce_metrics(
    db: Session, tenant_id: str, start: Date, end: Date, modes: list[str] | None = None,
) -> WorkforceMetrics:
    """Real, not invented: only counts time between a job's own extraction-finished moment and
    an actual "Approved and Proceed" press (Job.validation_approved_at for GK1, or
    gk2_validation_approved_at for GK2's own separate pass) - see those columns' own
    docstrings. A job that sailed through Data Validation with no explicit approval (no
    cross-doc checks to confirm) contributes nothing here, rather than having time guessed for
    it - jobs_with_recorded_review says how many of total_jobs that actually covers.

    Scoped to jobs COMPLETED in this window (_completed_jobs_query), same population as Data
    Accuracy/Job Processing Time.
    """
    start_utc, end_utc = range_bounds(start, end)
    jobs = _completed_jobs_query(db, tenant_id, start_utc, end_utc, modes).all()

    out = WorkforceMetrics(total_jobs=len(jobs))
    if not jobs:
        return out

    user_ids: set[str] = set()
    per_user_hours: dict[str, float] = {}
    per_user_jobs: dict[str, set[str]] = {}
    jobs_with_review: set[str] = set()

    for job in jobs:
        extraction_end = _extraction_end(job)
        if extraction_end is None:
            continue

        gk1_end = job.validation_approved_at
        if gk1_end and job.validation_approved_by_id and gk1_end > extraction_end:
            hours = (gk1_end - extraction_end).total_seconds() / 3600
            uid = job.validation_approved_by_id
            per_user_hours[uid] = per_user_hours.get(uid, 0.0) + hours
            per_user_jobs.setdefault(uid, set()).add(job.id)
            user_ids.add(uid)
            jobs_with_review.add(job.id)

        gk2_end = job.gk2_validation_approved_at
        gk2_start = gk1_end or extraction_end
        if gk2_end and job.gk2_validation_approved_by_id and gk2_end > gk2_start:
            hours = (gk2_end - gk2_start).total_seconds() / 3600
            uid = job.gk2_validation_approved_by_id
            per_user_hours[uid] = per_user_hours.get(uid, 0.0) + hours
            per_user_jobs.setdefault(uid, set()).add(job.id)
            user_ids.add(uid)
            jobs_with_review.add(job.id)

    out.jobs_with_recorded_review = len(jobs_with_review)
    out.human_review_hours = round(sum(per_user_hours.values()), 1)

    if jobs_with_review:
        # Per-job total (a job with both a GK1 and a GK2 pass counts both toward its own
        # total), averaged across only the jobs that have any recorded time at all.
        per_job_hours: dict[str, float] = {}
        for job in jobs:
            if job.id not in jobs_with_review:
                continue
            total = 0.0
            extraction_end = _extraction_end(job)
            if job.validation_approved_at and job.validation_approved_by_id and extraction_end and job.validation_approved_at > extraction_end:
                total += (job.validation_approved_at - extraction_end).total_seconds() / 60
            gk2_start = job.validation_approved_at or extraction_end
            if job.gk2_validation_approved_at and job.gk2_validation_approved_by_id and gk2_start and job.gk2_validation_approved_at > gk2_start:
                total += (job.gk2_validation_approved_at - gk2_start).total_seconds() / 60
            per_job_hours[job.id] = total
        out.avg_review_minutes_per_job = _avg(list(per_job_hours.values()))

    working_hours = _working_hours_in_range(start, end)
    if working_hours > 0:
        out.employee_requirement_fte = round(out.human_review_hours / working_hours, 2)

    if user_ids:
        names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(user_ids)).all()}
        rows = [
            EmployeeReviewRow(
                user_id=uid,
                name=names.get(uid, "Unknown user"),
                jobs_reviewed=len(per_user_jobs.get(uid, ())),
                review_hours=round(per_user_hours.get(uid, 0.0), 1),
                avg_minutes_per_job=(
                    round(per_user_hours[uid] * 60 / len(per_user_jobs[uid]), 1)
                    if per_user_jobs.get(uid) else None
                ),
            )
            for uid in user_ids
        ]
        rows.sort(key=lambda r: -r.review_hours)
        out.by_employee = rows

    out.by_job = _job_timings(db, jobs)
    return out

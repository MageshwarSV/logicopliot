"""Tenant Admin Executive Dashboard — Data Accuracy and Job Processing Time pages only.

Workforce Productivity (FTE, review hours per employee) is deliberately NOT here: it would
need per-user, per-stage start/end timestamps the system does not record today (JobEvent has
no actor column; Job has no "review started/finished" pair the way it has erp_verified_at/
erp_verified_by_id for the one checkpoint that IS tracked). Faking those numbers on an
executive-facing dashboard would be worse than not showing them - see the Workforce
Productivity page once that tracking exists.

Both pages here are scoped to COMPLETED jobs only, anchored on Job.completed_at - the same
"what finished, and when" definition app/api/v1/manager_app.py already uses, so this dashboard
can never quietly disagree with the Manager app about what counts as done. A job still being
worked on contributes to neither page; it has no accuracy figure yet (still being corrected)
and no duration (not yet finished).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.api.v1.jobs import _category_condition
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
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
    q = (
        db.query(Job)
        .filter(Job.tenant_id == tenant_id)
        .filter(_category_condition("completed"))
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
class AccuracyMetrics:
    documents_processed: int = 0
    fields_extracted_by_ai: int = 0
    fields_manually_edited: int = 0
    accuracy_pct: float | None = None
    by_document_type: list[DocTypeAccuracy] = field(default_factory=list)
    trend: list[TrendPoint] = field(default_factory=list)


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
    """
    start_utc, end_utc = range_bounds(start, end)
    jobs_q = _completed_jobs_query(db, tenant_id, start_utc, end_utc, modes)
    job_ids = [r.id for r in jobs_q.with_entities(Job.id).all()]

    out = AccuracyMetrics()
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
class ProcessingTimeMetrics:
    total_jobs: int = 0
    avg_total_tat_minutes: float | None = None
    avg_extraction_minutes: float | None = None
    avg_review_minutes: float | None = None
    avg_erp_submission_minutes: float | None = None
    trend: list[TrendPoint] = field(default_factory=list)


def _job_stage_durations(job: Job) -> tuple[float | None, float | None, float | None]:
    """(extraction_minutes, review_minutes, erp_submission_minutes) for ONE completed job,
    reconstructed from JobEvent history plus the job's own checkpoint timestamps.

    Extraction: the LAST "extracting" JobEvent's own timestamp, to the next event after it
    (whatever it is) - covers a job re-extracted more than once, using its final pass.
    Review: that extraction end, to ai_submitted_at (the AI/GK2 path's hand-off point) - or,
    for a job submitted directly (ai_submitted_at never set), to completed_at itself, since a
    direct Submit Entry has no separate "review" vs "ERP entry" split; one human action did
    both in real time.
    ERP submission: ai_submitted_at to completed_at - only meaningful (and only returned) for
    a job that actually went through that path.
    """
    events = sorted(job.events, key=lambda e: e.created_at)
    extraction_start = extraction_end = None
    for i, ev in enumerate(events):
        if ev.status == "extracting":
            extraction_start = ev.created_at
            extraction_end = events[i + 1].created_at if i + 1 < len(events) else None
    extraction_minutes = None
    if extraction_start and extraction_end:
        extraction_minutes = (extraction_end - extraction_start).total_seconds() / 60

    review_end = job.ai_submitted_at or job.completed_at
    review_minutes = None
    if extraction_end and review_end and review_end > extraction_end:
        review_minutes = (review_end - extraction_end).total_seconds() / 60

    erp_minutes = None
    if job.ai_submitted_at and job.completed_at and job.completed_at > job.ai_submitted_at:
        erp_minutes = (job.completed_at - job.ai_submitted_at).total_seconds() / 60

    return extraction_minutes, review_minutes, erp_minutes


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compute_processing_time_metrics(
    db: Session, tenant_id: str, start: Date, end: Date, modes: list[str] | None = None,
) -> ProcessingTimeMetrics:
    start_utc, end_utc = range_bounds(start, end)
    jobs = _completed_jobs_query(db, tenant_id, start_utc, end_utc, modes).all()

    out = ProcessingTimeMetrics(total_jobs=len(jobs))
    if not jobs:
        out.trend = [TrendPoint(day=start + timedelta(days=i)) for i in range((end - start).days + 1)]
        return out

    tats, extractions, reviews, erps = [], [], [], []
    for j in jobs:
        tats.append((j.completed_at - j.created_at).total_seconds() / 60)
        ext, rev, erp = _job_stage_durations(j)
        if ext is not None:
            extractions.append(ext)
        if rev is not None:
            reviews.append(rev)
        if erp is not None:
            erps.append(erp)

    out.avg_total_tat_minutes = _avg(tats)
    out.avg_extraction_minutes = _avg(extractions)
    out.avg_review_minutes = _avg(reviews)
    out.avg_erp_submission_minutes = _avg(erps)

    by_day: dict[Date, list[float]] = {}
    for j in jobs:
        local_day = (j.completed_at + timedelta(minutes=IST_OFFSET_MINUTES)).date()
        by_day.setdefault(local_day, []).append((j.completed_at - j.created_at).total_seconds() / 60)

    cursor = start
    while cursor <= end:
        out.trend.append(TrendPoint(day=cursor, avg_total_tat_minutes=_avg(by_day.get(cursor, []))))
        cursor += timedelta(days=1)

    return out

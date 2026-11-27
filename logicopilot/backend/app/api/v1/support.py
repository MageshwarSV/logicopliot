"""Production Support - the support desk's own screen (S1, 2026-11-27).

Cross-tenant and read-only, built only from data the system already keeps:

  GET /support/overview  - for the jobs that ARRIVED in the period: tiles (jobs, pages,
                           attachments, submitted, failed), where those jobs stand now
                           (pipeline), jobs and pages per day, and the open problems
                           ("attention") across every customer.
  GET /support/issues    - the open problems as a list: failed ERP entries, extractions that
                           did not finish or are taking too long, jobs waiting on documents -
                           each with the reason in plain words and whether Support may retry.

Retry itself is the existing endpoints (gk2/approve for the ERP, extract for an unfinished
extraction), which accept the support role on a failed job only - see jobs.py.

Not here yet (later phases): server health (S2), stage timings / SLA / delayed jobs (S3),
notes and customer-call log (S4).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.models.job import Job, JobDocument
from app.models.job_event import JobEvent
from app.models.job_failure import JobFailureReport
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, SUPPORT, User

router = APIRouter(prefix="/support", tags=["support"])

IST = timezone(timedelta(hours=5, minutes=30))
# SLA agreed 2026-11-27 (mock-up values). Only the extraction one is used in S1: a job still
# "extracting" this long is shown as taking too long. The rest arrive with stage timings (S3).
EXTRACTION_STUCK_MIN = 4
# An unfinished extraction is reported only after this long, so a job still arriving or just
# queued is not called failed.
EXTRACTION_FAILED_AFTER_MIN = 10
# Open problems are looked for among jobs that arrived this many days back.
ISSUE_WINDOW_DAYS = 60

TABS = ("all", "extraction", "erp", "other")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _period(date_from: str, date_to: str) -> tuple[date, date, datetime, datetime]:
    try:
        d_from, d_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=422, detail="date_from and date_to must be YYYY-MM-DD")
    if d_to < d_from:
        raise HTTPException(status_code=422, detail="date_to must not be before date_from")
    if (d_to - d_from).days > 366:
        raise HTTPException(status_code=422, detail="Pick a range of 366 days or fewer")
    start = datetime(d_from.year, d_from.month, d_from.day, tzinfo=IST).astimezone(timezone.utc)
    end = (datetime(d_to.year, d_to.month, d_to.day, tzinfo=IST) + timedelta(days=1)).astimezone(timezone.utc)
    return d_from, d_to, start, end


def _is_failed(j: Job) -> bool:
    """The red "Failed" badge's own rule (jobs._category_condition("failed"))."""
    return j.gk2_status == "failed" or j.status == "failed"


def _is_submitted(j: Job) -> bool:
    """The "ERP Submitted" rule (jobs._category_condition("submitted"))."""
    return ((j.ai_submitted_at is not None and j.status != "failed")
            or (j.ai_submitted_at is None and j.status == "completed"))


def _pipeline_stage(j: Job) -> str:
    """Where a job stands now, in the pipeline's own words. Failed and submitted first, as on
    the badge."""
    if _is_failed(j):
        return "failed"
    if _is_submitted(j):
        return "erp_submitted"
    if j.gk2_status in ("preparing_erp", "entering_erp", "submitted"):
        return "erp_submission"
    if j.gk2_status in ("pending", "irn_document_process"):
        return "waiting_approval"
    if j.status == "extracting":
        return "ai_processing"
    if j.status == "draft":
        return "received"
    if j.status == "extracted":
        return "in_review" if j.review_started_at is not None else "ready_for_review"
    return "other"


PIPELINE = [
    ("received", "Received"),
    ("ai_processing", "AI Processing"),
    ("ready_for_review", "Ready for Review"),
    ("in_review", "In Review"),
    ("waiting_approval", "Waiting Approval"),
    ("erp_submission", "ERP Submission"),
    ("erp_submitted", "ERP Submitted"),
    ("failed", "Failed"),
]


def _last_event_at(db: Session, job_ids: list[str], status: str) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    if not job_ids:
        return out
    for jid, at in (db.query(JobEvent.job_id, JobEvent.created_at)
                    .filter(JobEvent.job_id.in_(job_ids), JobEvent.status == status).all()):
        at = _aware(at)
        if at and (jid not in out or at > out[jid]):
            out[jid] = at
    return out


def _reason(j: Job) -> str:
    """The failure in plain words: the ERP-rejection card's first item when there is one."""
    help_ = j.erp_help or {}
    items = help_.get("items") if isinstance(help_, dict) else None
    if items:
        first = items[0]
        text = f"ERP did not accept {first.get('display')}: {first.get('needs')}"
        if len(items) > 1:
            text += f" (+{len(items) - 1} more)"
        return text[:240]
    if j.erp_diagnosis:
        return j.erp_diagnosis[:240]
    return (j.erp_reason or "ERP entry failed")[:240]


def _issues(db: Session) -> list[dict]:
    """Every open problem among recent jobs, newest problem first."""
    from app.api.v1.jobs import _is_missing_documents

    now = _now()
    since = now - timedelta(days=ISSUE_WINDOW_DAYS)
    jobs = (db.query(Job)
            .filter(Job.created_at >= since, Job.status != "completed")
            .all())
    if not jobs:
        return []
    groups = {g.id: g for g in db.query(TemplateGroup).all()}
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    pages: Counter = Counter()
    for jid, pc in (db.query(JobDocument.job_id, JobDocument.page_count)
                    .filter(JobDocument.job_id.in_([j.id for j in jobs])).all()):
        pages[jid] += pc or 0
    extracting = [j.id for j in jobs if j.status == "extracting"]
    started = _last_event_at(db, extracting, "extracting")

    out: list[dict] = []
    for j in jobs:
        kind = None
        since_at = _aware(j.created_at)
        if _is_failed(j):
            kind = "erp_failed"
            since_at = _aware(j.erp_failed_at) or _aware(j.updated_at) or since_at
            reason = _reason(j)
        elif j.status == "extracting":
            at = started.get(j.id) or _aware(j.updated_at) or since_at
            if at and now - at > timedelta(minutes=EXTRACTION_STUCK_MIN):
                kind, since_at = "extraction_slow", at
                reason = f"Still reading the documents after {EXTRACTION_STUCK_MIN} minutes"
        elif j.status == "draft" and since_at and now - since_at > timedelta(minutes=EXTRACTION_FAILED_AFTER_MIN):
            try:
                missing = _is_missing_documents(db, j)
            except Exception:  # noqa: BLE001
                missing = False
            if missing:
                kind, reason = "missing_documents", "Waiting for required documents"
            elif any(d.file_path for d in j.documents):
                kind, reason = "extraction_failed", "Extraction did not finish"
        if kind is None:
            continue
        g = groups.get(j.group_id)
        out.append({
            "job_id": j.id,
            "reference": j.reference,
            "client": tenants.get(j.tenant_id) or "",
            "type": (g.mode if g is not None else None) or "",
            "template": g.name if g is not None else "",
            "kind": kind,
            "tab": "erp" if kind == "erp_failed" else ("other" if kind == "missing_documents" else "extraction"),
            "stage": {"erp_failed": "ERP Submission", "extraction_slow": "Extraction",
                      "extraction_failed": "Extraction", "missing_documents": "Documents"}[kind],
            "pages": pages[j.id],
            "reason": reason,
            "since": since_at.isoformat() if since_at else None,
            "age_minutes": int((now - since_at).total_seconds() // 60) if since_at else None,
            # What Support may press - the endpoints check the same rules again.
            "can_retry_erp": kind == "erp_failed" and j.gk2_status == "failed",
            "can_retry_extraction": kind == "extraction_failed",
        })
    out.sort(key=lambda r: r["since"] or "", reverse=True)
    return out


@router.get("/overview")
def overview(
    date_from: str,
    date_to: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(SUPER_ADMIN, SUPPORT)),
) -> dict:
    d_from, d_to, start, end = _period(date_from, date_to)
    jobs = db.query(Job).filter(Job.created_at >= start, Job.created_at < end).all()
    ids = [j.id for j in jobs]
    pages_by_job: Counter = Counter()
    attachments = 0
    if ids:
        for jid, pc, fp in (db.query(JobDocument.job_id, JobDocument.page_count, JobDocument.file_path)
                            .filter(JobDocument.job_id.in_(ids)).all()):
            pages_by_job[jid] += pc or 0
            if fp:
                attachments += 1
    total = len(jobs)
    submitted = sum(1 for j in jobs if _is_submitted(j) and not _is_failed(j))
    failed = sum(1 for j in jobs if _is_failed(j))

    stages = Counter(_pipeline_stage(j) for j in jobs)
    per_day: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "pages": 0})
    d = d_from
    while d <= d_to:
        per_day[d.isoformat()]
        d += timedelta(days=1)
    for j in jobs:
        day = _aware(j.created_at).astimezone(IST).date().isoformat()
        per_day[day]["jobs"] += 1
        per_day[day]["pages"] += pages_by_job[j.id]

    issues = _issues(db)
    kinds = Counter(i["kind"] for i in issues)
    open_reports = db.query(JobFailureReport).filter(JobFailureReport.status == "open").count()

    def pct(n: int) -> float | None:
        return round(100.0 * n / total, 1) if total else None

    return {
        "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
        "tiles": {
            "total_jobs": total,
            "pages": sum(pages_by_job.values()),
            "avg_pages": round(sum(pages_by_job.values()) / total, 1) if total else None,
            "attachments": attachments,
            "submitted": submitted, "submitted_pct": pct(submitted),
            "failed": failed, "failed_pct": pct(failed),
        },
        "pipeline": [{"key": k, "label": label, "count": stages.get(k, 0)} for k, label in PIPELINE],
        "volume": [{"day": day, **v} for day, v in sorted(per_day.items())],
        "attention": [
            {"key": "erp_failed", "label": "Failed jobs (action needed)", "count": kinds.get("erp_failed", 0)},
            {"key": "extraction_failed", "label": "Extraction did not finish", "count": kinds.get("extraction_failed", 0)},
            {"key": "extraction_slow", "label": f"Extraction taking > {EXTRACTION_STUCK_MIN} min", "count": kinds.get("extraction_slow", 0)},
            {"key": "erp_reports", "label": "Open ERP failure reports", "count": open_reports},
            {"key": "missing_documents", "label": "Waiting for documents", "count": kinds.get("missing_documents", 0)},
        ],
    }


@router.get("/issues")
def issues(
    tab: str = "all",
    db: Session = Depends(get_db),
    _user: User = Depends(require_role(SUPER_ADMIN, SUPPORT)),
) -> dict:
    if tab not in TABS:
        raise HTTPException(status_code=422, detail=f"tab must be one of: {', '.join(TABS)}")
    rows = _issues(db)
    counts = Counter(r["tab"] for r in rows)
    return {
        "counts": {"all": len(rows), **{t: counts.get(t, 0) for t in TABS if t != "all"}},
        "items": [r for r in rows if tab == "all" or r["tab"] == tab],
    }

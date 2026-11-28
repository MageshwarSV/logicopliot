"""Production Support - the support desk's own screen (S1-S5, 2026-11-27). Cross-tenant.

  GET  /support/overview      tiles (incl. delayed), pipeline, jobs per day, attention, recent activity
  GET  /support/issues        open problems: failed ERP, extraction not finished / slow, ERP slow,
                              waiting for documents - reason, age, who may retry; ?include_handled
  POST /support/issues/{job_id}/handled   "handled" + note; hides it until it fails again
  GET  /support/performance   average / slowest time per stage vs SLA, delayed jobs (S3)
  GET  /support/health        Application Health boxes (S2, app/core/support_health.py)
  GET  /support/jobs          Job Monitoring - every customer's jobs, filters
  GET  /support/customers     Customer View - per customer totals and open problems
  GET  /support/activity      the support log; POST adds a note / customer call (S4)
  GET/PUT /support/settings   SLA minutes, alert emails (S5); POST /support/alerts/test

Retry is the existing job endpoints (gk2/approve, extract), which accept the support role on a
failed job only - see jobs.py.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role
from app.core.support_alerts import DEFAULT_SLA, get_config
from app.models.job import Job, JobDocument
from app.models.job_event import JobEvent
from app.models.job_failure import JobFailureReport
from app.models.support_activity import SupportActivity
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, SUPPORT, User

router = APIRouter(prefix="/support", tags=["support"])

IST = timezone(timedelta(hours=5, minutes=30))
# An unfinished extraction is called failed only after this long, so a job still arriving or
# just queued is not.
EXTRACTION_FAILED_AFTER_MIN = 10
# Open problems are looked for among jobs that arrived this many days back.
ISSUE_WINDOW_DAYS = 60
TABS = ("all", "extraction", "erp", "other")
STAGES = ("email_to_ai", "queue_wait", "extraction", "erp")
STAGE_LABEL = {"email_to_ai": "Email to AI start", "queue_wait": "Queue waiting time",
               "extraction": "AI extraction (per job)", "erp": "ERP submission"}

_desk = require_role(SUPER_ADMIN, SUPPORT)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minutes(a: datetime | None, b: datetime | None) -> float | None:
    a, b = _aware(a), _aware(b)
    if a is None or b is None or b < a:
        return None
    return round((b - a).total_seconds() / 60, 2)


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
    ("received", "Received"), ("ai_processing", "AI Processing"), ("ready_for_review", "Ready for Review"),
    ("in_review", "In Review"), ("waiting_approval", "Waiting Approval"), ("erp_submission", "ERP Submission"),
    ("erp_submitted", "ERP Submitted"), ("failed", "Failed"),
]
PIPELINE_LABEL = dict(PIPELINE)


def _extracting_events(db: Session, job_ids: list[str]) -> dict[str, list[datetime]]:
    out: dict[str, list[datetime]] = defaultdict(list)
    for chunk in range(0, len(job_ids), 500):
        ids = job_ids[chunk:chunk + 500]
        for jid, at in (db.query(JobEvent.job_id, JobEvent.created_at)
                        .filter(JobEvent.job_id.in_(ids), JobEvent.status == "extracting").all()):
            if at:
                out[jid].append(_aware(at))
    for v in out.values():
        v.sort()
    return out


def _timings(j: Job, events: list[datetime]) -> dict[str, float | None]:
    """Minutes per stage for one job (None when not measurable).

    email_to_ai  job created -> its first "extracting" event
    queue_wait   the "extracting" event of the latest run -> reading actually began
    extraction   reading began -> finished (extraction_started_at / _finished_at, new 2026-11-27)
    erp          ERP run started (erp_submitted_at) -> it ended (submitted or failed)
    """
    first = events[0] if events else None
    started = _aware(j.extraction_started_at)
    run_event = max((e for e in events if started and e <= started), default=None)
    end_candidates = [t for t in (_aware(j.ai_submitted_at), _aware(j.erp_failed_at))
                      if t and j.erp_submitted_at and t >= _aware(j.erp_submitted_at)]
    return {
        "email_to_ai": _minutes(j.created_at, first),
        "queue_wait": _minutes(run_event, started),
        "extraction": _minutes(started, j.extraction_finished_at),
        "erp": _minutes(j.erp_submitted_at, min(end_candidates)) if end_candidates else None,
    }


def _breaches(t: dict, sla: dict) -> list[str]:
    return [k for k in STAGES if t.get(k) is not None and t[k] > sla[k]]


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


def _handled_at(db: Session, job_ids: list[str]) -> dict[str, tuple[datetime, str]]:
    out: dict[str, tuple[datetime, str]] = {}
    if not job_ids:
        return out
    for a in (db.query(SupportActivity).filter(SupportActivity.kind == "handled",
                                               SupportActivity.job_id.in_(job_ids)).all()):
        at = _aware(a.created_at)
        if a.job_id not in out or at > out[a.job_id][0]:
            out[a.job_id] = (at, a.text or "")
    return out


def _issues(db: Session, include_handled: bool = False) -> list[dict]:
    """Every open problem among recent jobs, newest problem first."""
    from app.api.v1.jobs import _is_missing_documents

    sla = get_config(db)["sla"]
    now = _now()
    jobs = (db.query(Job)
            .filter(Job.created_at >= now - timedelta(days=ISSUE_WINDOW_DAYS), Job.status != "completed")
            .all())
    if not jobs:
        return []
    groups = {g.id: g for g in db.query(TemplateGroup).all()}
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    pages: Counter = Counter()
    for jid, pc in (db.query(JobDocument.job_id, JobDocument.page_count)
                    .filter(JobDocument.job_id.in_([j.id for j in jobs])).all()):
        pages[jid] += pc or 0
    started = {jid: v[-1] for jid, v in _extracting_events(db, [j.id for j in jobs if j.status == "extracting"]).items()}
    handled = _handled_at(db, [j.id for j in jobs])

    out: list[dict] = []
    for j in jobs:
        kind, reason = None, ""
        since_at = _aware(j.created_at)
        if _is_failed(j):
            kind = "erp_failed"
            since_at = _aware(j.erp_failed_at) or _aware(j.updated_at) or since_at
            reason = _reason(j)
        elif j.gk2_status in ("preparing_erp", "entering_erp"):
            at = _aware(j.erp_submitted_at) or _aware(j.updated_at)
            if at and now - at > timedelta(minutes=sla["erp"]):
                kind, since_at = "erp_slow", at
                reason = f"ERP entry still running after {sla['erp']} minutes"
        elif j.status == "extracting":
            at = started.get(j.id) or _aware(j.updated_at) or since_at
            if at and now - at > timedelta(minutes=sla["extraction"]):
                kind, since_at = "extraction_slow", at
                reason = f"Still reading the documents after {sla['extraction']} minutes"
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
        h = handled.get(j.id)
        is_handled = bool(h and since_at and h[0] >= since_at)
        if is_handled and not include_handled:
            continue
        g = groups.get(j.group_id)
        out.append({
            "job_id": j.id, "reference": j.reference, "client": tenants.get(j.tenant_id) or "",
            "tenant_id": j.tenant_id, "type": (g.mode if g is not None else None) or "",
            "template": g.name if g is not None else "", "kind": kind,
            "tab": "erp" if kind in ("erp_failed", "erp_slow") else ("other" if kind == "missing_documents" else "extraction"),
            "stage": {"erp_failed": "ERP Submission", "erp_slow": "ERP Submission", "extraction_slow": "Extraction",
                      "extraction_failed": "Extraction", "missing_documents": "Documents"}[kind],
            "pages": pages[j.id], "reason": reason,
            "since": since_at.isoformat() if since_at else None,
            "age_minutes": int((now - since_at).total_seconds() // 60) if since_at else None,
            # When it arrived, when it first went to the ERP (retries keep this) and the last
            # try - so the desk sees how long a customer has been waiting on the ERP.
            "created_at": _aware(j.created_at).isoformat() if j.created_at else None,
            "first_erp_at": _aware(j.erp_first_submitted_at).isoformat() if j.erp_first_submitted_at else None,
            "last_erp_at": _aware(j.erp_submitted_at).isoformat() if j.erp_submitted_at else None,
            "since_first_erp_minutes": (int((now - _aware(j.erp_first_submitted_at)).total_seconds() // 60)
                                        if j.erp_first_submitted_at else None),
            "can_retry_erp": kind == "erp_failed" and j.gk2_status == "failed",
            "can_retry_extraction": kind == "extraction_failed",
            "handled": is_handled, "handled_note": h[1] if is_handled else None,
        })
    out.sort(key=lambda r: r["since"] or "", reverse=True)
    return out


def _activity_out(db: Session, rows: list[SupportActivity]) -> list[dict]:
    users = {u.id: (u.full_name or u.email) for u in db.query(User).filter(
        User.id.in_([r.user_id for r in rows if r.user_id])).all()} if rows else {}
    tenants = {t.id: t.name for t in db.query(Tenant).all()} if rows else {}
    refs = {j.id: j.reference for j in db.query(Job).filter(
        Job.id.in_([r.job_id for r in rows if r.job_id])).all()} if rows else {}
    return [{"id": r.id, "kind": r.kind, "text": r.text, "at": _aware(r.created_at).isoformat(),
             "by": users.get(r.user_id) if r.user_id else ("System" if r.kind == "alert" else None),
             "client": tenants.get(r.tenant_id) if r.tenant_id else None,
             "job_id": r.job_id, "reference": refs.get(r.job_id) if r.job_id else None} for r in rows]


def _recent_activity(db: Session, limit: int = 10, kind: str | None = None, job_id: str | None = None) -> list[dict]:
    q = db.query(SupportActivity)
    if kind:
        q = q.filter(SupportActivity.kind == kind)
    if job_id:
        q = q.filter(SupportActivity.job_id == job_id)
    return _activity_out(db, q.order_by(SupportActivity.created_at.desc()).limit(limit).all())


# ---------------------------------------------------------------- overview

@router.get("/overview")
def overview(date_from: str, date_to: str, db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    d_from, d_to, start, end = _period(date_from, date_to)
    sla = get_config(db)["sla"]
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
    submitted_jobs = [j for j in jobs if _is_submitted(j) and not _is_failed(j)]
    submitted = len(submitted_jobs)
    # Auto ERP Submission vs entered after a person approved it (2026-11-28, replaces the
    # Attachments tile on the overview).
    auto_submitted = sum(1 for j in submitted_jobs if getattr(j, "auto_erp_submitted", False))
    manual_submitted = submitted - auto_submitted
    # Not submitted yet, not failed: an auto job is on its way into the ERP; a manual one is
    # still with a person (review / approval / their own ERP run).
    open_jobs = [j for j in jobs if not _is_failed(j) and not (_is_submitted(j) and not _is_failed(j))]
    auto_in_progress = sum(1 for j in open_jobs if getattr(j, "auto_erp_submitted", False))
    manual_in_review = sum(1 for j in open_jobs if not getattr(j, "auto_erp_submitted", False)
                           and _pipeline_stage(j) in ("ready_for_review", "in_review", "waiting_approval",
                                                      "erp_submission"))
    failed = sum(1 for j in jobs if _is_failed(j))
    events = _extracting_events(db, ids)
    delayed = sum(1 for j in jobs if _breaches(_timings(j, events.get(j.id, [])), sla))

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
    # Open failure reports whose job is still failed and recent - not every report nobody
    # ever closed (SIT showed 155 of those, 2026-11-27).
    failed_ids = {i["job_id"] for i in issues if i["kind"] == "erp_failed"}
    open_reports = 0
    if failed_ids:
        open_reports = (db.query(JobFailureReport)
                        .filter(JobFailureReport.status == "open", JobFailureReport.job_id.in_(failed_ids))
                        .with_entities(JobFailureReport.job_id).distinct().count())

    def pct(n: int) -> float | None:
        return round(100.0 * n / total, 1) if total else None

    return {
        "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
        "tiles": {
            "total_jobs": total, "pages": sum(pages_by_job.values()),
            "avg_pages": round(sum(pages_by_job.values()) / total, 1) if total else None,
            "attachments": attachments,
            "submitted": submitted, "submitted_pct": pct(submitted),
            "auto_submitted": auto_submitted, "manual_submitted": manual_submitted,
            "auto_in_progress": auto_in_progress, "manual_in_review": manual_in_review,
            "auto_submitted_pct": round(100.0 * auto_submitted / submitted, 1) if submitted else None,
            "manual_submitted_pct": round(100.0 * manual_submitted / submitted, 1) if submitted else None,
            "failed": failed, "failed_pct": pct(failed),
            "delayed": delayed, "delayed_pct": pct(delayed),
        },
        "pipeline": [{"key": k, "label": label, "count": stages.get(k, 0)} for k, label in PIPELINE],
        "volume": [{"day": day, **v} for day, v in sorted(per_day.items())],
        "attention_window_days": ISSUE_WINDOW_DAYS,
        "attention": [
            {"key": "erp_failed", "label": "Failed jobs (action needed)", "count": kinds.get("erp_failed", 0)},
            {"key": "erp_slow", "label": f"ERP entry running > {sla['erp']} min", "count": kinds.get("erp_slow", 0)},
            {"key": "extraction_failed", "label": "Extraction did not finish", "count": kinds.get("extraction_failed", 0)},
            {"key": "extraction_slow", "label": f"Extraction taking > {sla['extraction']} min", "count": kinds.get("extraction_slow", 0)},
            {"key": "erp_reports", "label": "Failed jobs with an open ERP failure report", "count": open_reports},
            {"key": "missing_documents", "label": "Waiting for documents", "count": kinds.get("missing_documents", 0)},
        ],
        "activity": _recent_activity(db, 8),
    }


@router.get("/issues")
def issues(tab: str = "all", include_handled: bool = False, db: Session = Depends(get_db),
           _user: User = Depends(_desk)) -> dict:
    if tab not in TABS:
        raise HTTPException(status_code=422, detail=f"tab must be one of: {', '.join(TABS)}")
    rows = _issues(db, include_handled=include_handled)
    counts = Counter(r["tab"] for r in rows)
    return {"counts": {"all": len(rows), **{t: counts.get(t, 0) for t in TABS if t != "all"}},
            "items": [r for r in rows if tab == "all" or r["tab"] == tab]}


class NoteIn(BaseModel):
    text: str = ""


@router.post("/issues/{job_id}/handled")
def mark_handled(job_id: str, payload: NoteIn, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    """The desk has dealt with this problem (told the customer, fixed outside Cargora...). The
    job leaves the list until it fails again; its open ERP failure reports are closed."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    note = (payload.text or "").strip()
    db.add(SupportActivity(kind="handled", tenant_id=job.tenant_id, job_id=job.id, user_id=user.id,
                           text=f"{job.reference} - marked handled" + (f": {note}" if note else "")))
    db.query(JobFailureReport).filter(JobFailureReport.job_id == job.id, JobFailureReport.status == "open") \
        .update({"status": "resolved"}, synchronize_session=False)
    db.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id, status=job.status,
                    note=f"Production Support marked this handled - {user.full_name or user.email}"
                         + (f": {note}" if note else "")))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- performance (S3)

@router.get("/performance")
def performance(date_from: str, date_to: str, db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    _f, _t, start, end = _period(date_from, date_to)
    sla = get_config(db)["sla"]
    jobs = db.query(Job).filter(Job.created_at >= start, Job.created_at < end).all()
    events = _extracting_events(db, [j.id for j in jobs])
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    per_stage: dict[str, list[float]] = defaultdict(list)
    delayed = []
    for j in jobs:
        t = _timings(j, events.get(j.id, []))
        for k in STAGES:
            if t[k] is not None:
                per_stage[k].append(t[k])
        b = _breaches(t, sla)
        if b:
            delayed.append({"job_id": j.id, "reference": j.reference, "client": tenants.get(j.tenant_id) or "",
                            "stages": [{"stage": STAGE_LABEL[k], "minutes": t[k], "sla": sla[k]} for k in b],
                            "created_at": _aware(j.created_at).isoformat()})
    stages = []
    for k in STAGES:
        vals = sorted(per_stage[k])
        avg = round(sum(vals) / len(vals), 1) if vals else None
        p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))] if vals else None
        stages.append({"key": k, "label": STAGE_LABEL[k], "sla": sla[k], "measured": len(vals),
                       "avg": avg, "p90": round(p90, 1) if p90 is not None else None,
                       "over_sla": sum(1 for v in vals if v > sla[k]),
                       "ok": avg is None or avg <= sla[k]})
    delayed.sort(key=lambda r: r["created_at"], reverse=True)
    return {"stages": stages, "delayed": delayed[:200], "delayed_count": len(delayed), "jobs": len(jobs),
            "note": "Extraction and queue times are recorded from 27 Nov 2026; older jobs have none."}


# ---------------------------------------------------------------- health (S2)

@router.get("/health")
def health(db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    from app.core.support_health import components

    return components(db)


# ---------------------------------------------------------------- job monitoring / customers (S5)

JOB_VIEWS = {
    "submitted": "Submitted to ERP", "auto": "Auto submitted", "manual": "Manually submitted",
    "auto_progress": "Auto - being entered now", "in_review": "Manual - in review",
    "failed": "Failed", "delayed": "Delayed (over SLA)",
}
_REVIEW_STAGES = ("ready_for_review", "in_review", "waiting_approval", "erp_submission")


def _in_view(j: Job, view: str, delayed: bool) -> bool:
    """The overview tiles' own definitions (overview()), so a tile and its list always agree."""
    auto = bool(getattr(j, "auto_erp_submitted", False))
    submitted = _is_submitted(j) and not _is_failed(j)
    open_ = not _is_failed(j) and not submitted
    return {
        "submitted": submitted, "auto": submitted and auto, "manual": submitted and not auto,
        "auto_progress": open_ and auto, "in_review": open_ and not auto and _pipeline_stage(j) in _REVIEW_STAGES,
        "failed": _is_failed(j), "delayed": delayed,
    }.get(view, True)


@router.get("/jobs")
def jobs_list(date_from: str, date_to: str, tenant_id: str | None = None, stage: str | None = None,
              q: str | None = None, view: str | None = None, limit: int = 300, db: Session = Depends(get_db),
              _user: User = Depends(_desk)) -> dict:
    _f, _t, start, end = _period(date_from, date_to)
    query = db.query(Job).filter(Job.created_at >= start, Job.created_at < end)
    if tenant_id:
        query = query.filter(Job.tenant_id == tenant_id)
    if q and q.strip():
        query = query.filter(Job.reference.ilike(f"%{q.strip()}%"))
    jobs = query.order_by(Job.created_at.desc()).all()
    if stage:
        jobs = [j for j in jobs if _pipeline_stage(j) == stage]
    sla = get_config(db)["sla"]
    if view and view in JOB_VIEWS:
        ev_all = _extracting_events(db, [j.id for j in jobs]) if view == "delayed" else {}
        jobs = [j for j in jobs if _in_view(j, view, view == "delayed" and bool(_breaches(_timings(j, ev_all.get(j.id, [])), sla)))]
    total = len(jobs)
    jobs = jobs[:max(1, min(limit, 1000))]
    groups = {g.id: g for g in db.query(TemplateGroup).all()}
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    events = _extracting_events(db, [j.id for j in jobs])
    # Who has the job: the person who sent it to the ERP, else the operator it is assigned to,
    # else who created it; "Auto ERP" when the system sent it itself.
    uids = {u for j in jobs for u in (j.erp_submitted_by_id, j.assigned_operator_id, j.created_by_id) if u}
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    pages: Counter = Counter()
    for jid, pc in (db.query(JobDocument.job_id, JobDocument.page_count)
                    .filter(JobDocument.job_id.in_([j.id for j in jobs])).all()) if jobs else []:
        pages[jid] += pc or 0
    rows = []
    for j in jobs:
        g = groups.get(j.group_id)
        st = _pipeline_stage(j)
        t = _timings(j, events.get(j.id, []))
        if getattr(j, "auto_erp_submitted", False):
            user, user_how = "Auto ERP", "sent by the system"
        elif j.erp_submitted_by_id and names.get(j.erp_submitted_by_id):
            user, user_how = names[j.erp_submitted_by_id], "submitted to ERP"
        elif j.assigned_operator_id and names.get(j.assigned_operator_id):
            user, user_how = names[j.assigned_operator_id], "assigned"
        elif j.created_by_id and names.get(j.created_by_id):
            user, user_how = names[j.created_by_id], "created"
        else:
            user, user_how = None, None
        rows.append({"job_id": j.id, "reference": j.reference, "client": tenants.get(j.tenant_id) or "",
                     "type": (g.mode if g else None) or "", "template": g.name if g else "",
                     "stage": st, "stage_label": PIPELINE_LABEL.get(st, "Other"),
                     "pages": pages[j.id], "created_at": _aware(j.created_at).isoformat(),
                     "timings": t, "delayed": bool(_breaches(t, sla)),
                     "user": user, "user_how": user_how, "auto": bool(getattr(j, "auto_erp_submitted", False))})
    return {"total": total, "items": rows, "stages": [{"key": k, "label": v} for k, v in PIPELINE],
            "views": [{"key": k, "label": v} for k, v in JOB_VIEWS.items()],
            "customers": [{"id": tid, "name": n} for tid, n in sorted(tenants.items(), key=lambda x: x[1].lower())]}


@router.get("/customers")
def customers(date_from: str, date_to: str, db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    _f, _t, start, end = _period(date_from, date_to)
    jobs = db.query(Job).filter(Job.created_at >= start, Job.created_at < end).all()
    open_by_tenant = Counter(i["tenant_id"] for i in _issues(db))
    rows: dict[str, dict] = {}
    for t in db.query(Tenant).all():
        rows[t.id] = {"tenant_id": t.id, "name": t.name, "jobs": 0, "submitted": 0, "failed": 0,
                      "in_progress": 0, "last_job_at": None, "open_issues": open_by_tenant.get(t.id, 0)}
    for j in jobs:
        r = rows.get(j.tenant_id)
        if r is None:
            continue
        r["jobs"] += 1
        if _is_failed(j):
            r["failed"] += 1
        elif _is_submitted(j):
            r["submitted"] += 1
        else:
            r["in_progress"] += 1
    for tid, at in db.query(Job.tenant_id, Job.created_at).order_by(Job.created_at.desc()).limit(5000).all():
        if tid in rows and rows[tid]["last_job_at"] is None and at:
            rows[tid]["last_job_at"] = _aware(at).isoformat()
    items = sorted(rows.values(), key=lambda r: (-r["open_issues"], -r["jobs"], r["name"].lower()))
    return {"items": [r for r in items if r["jobs"] or r["open_issues"] or r["last_job_at"]]}


# ---------------------------------------------------------------- activity (S4)

class ActivityIn(BaseModel):
    kind: str = "note"
    text: str
    tenant_id: str | None = None
    job_id: str | None = None


@router.get("/activity")
def activity(limit: int = 50, kind: str | None = None, job_id: str | None = None,
             db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    return {"items": _recent_activity(db, max(1, min(limit, 500)), kind or None, job_id or None)}


@router.post("/activity")
def add_activity(payload: ActivityIn, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    if payload.kind not in ("note", "customer_call"):
        raise HTTPException(status_code=422, detail="kind must be note or customer_call")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write what happened")
    tenant_id = payload.tenant_id or None
    if payload.job_id:
        job = db.get(Job, payload.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        tenant_id = job.tenant_id
    if tenant_id and db.get(Tenant, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    row = SupportActivity(kind=payload.kind, text=text[:4000], tenant_id=tenant_id,
                          job_id=payload.job_id or None, user_id=user.id)
    db.add(row)
    db.commit()
    return _activity_out(db, [row])[0]


# ---------------------------------------------------------------- settings / alerts (S5)

class SettingsIn(BaseModel):
    sla: dict | None = None
    alert_emails: list[str] | None = None


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    from app.core.support_alerts import email_configured

    return {**get_config(db), "defaults": DEFAULT_SLA, "email_configured": email_configured(),
            "labels": STAGE_LABEL}


@router.put("/settings")
def put_settings(payload: SettingsIn, db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    from app.core.support_alerts import email_configured, set_config

    try:
        cfg = set_config(db, payload.sla, payload.alert_emails)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="SLA values must be whole minutes")
    return {**cfg, "defaults": DEFAULT_SLA, "email_configured": email_configured(), "labels": STAGE_LABEL}


@router.post("/alerts/test")
def test_alert(db: Session = Depends(get_db), _user: User = Depends(_desk)) -> dict:
    from app.core.support_alerts import send_email

    to = get_config(db)["alert_emails"]
    try:
        send_email(to, "Cargora alert test", "This is a test of Production Support alerts. If you can read it, alert emails work.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not send: {exc}")
    return {"ok": True, "sent_to": to}

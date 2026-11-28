"""Support mobile app API (2026-11-28) - what the Cargora Support phone app reads and writes.

Login is the normal /auth/login with token_response=true (the Bearer token the Manager app
already uses); every endpoint here is SUPPORT / SUPER_ADMIN only.

  GET  /support/app/home                       health in one line, today's counts, open problems, last alerts
  GET  /support/app/issues                     open problems (+ handled) with known-issue fix and who is on it
  GET  /support/app/jobs/{job_id}              one job: why, the ERP's corrections, timeline, notes, fix
  POST /support/app/jobs/{job_id}/notes        {text, outcome} - what the desk did
  GET  /support/app/known-issues               open problems grouped by cause, with the saved fix
  PUT  /support/app/known-issues/{key}         {title, fix} - write the fix once
  POST /support/app/devices                    {token, platform} - this phone gets push alerts
  POST /support/app/devices/remove             {token} - on logout

Mark handled and Retry are the existing endpoints (/support/issues/{id}/handled,
/jobs/{id}/gk2/approve, /jobs/{id}/extract), so the website and the phone behave the same.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1 import support as desk
from app.core import support_app as sa_core
from app.core.deps import get_db, require_role
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.support_activity import SupportActivity
from app.models.support_app import SupportDevice, SupportKnownIssue
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, SUPPORT, User

router = APIRouter(prefix="/support/app", tags=["support app"])
_desk = require_role(SUPER_ADMIN, SUPPORT)


def _iso(dt: datetime | None) -> str | None:
    dt = desk._aware(dt)
    return dt.isoformat() if dt else None


def _known(db: Session) -> dict[str, SupportKnownIssue]:
    return {k.key: k for k in db.query(SupportKnownIssue).all()}


def _latest_notes(db: Session, job_ids: list[str]) -> dict[str, dict]:
    """The newest note / handled / retry per job - "who is on it"."""
    out: dict[str, dict] = {}
    if not job_ids:
        return out
    rows = (db.query(SupportActivity)
            .filter(SupportActivity.job_id.in_(job_ids),
                    SupportActivity.kind.in_(("note", "customer_call", "handled", "retry_erp", "retry_extraction")))
            .order_by(SupportActivity.created_at.desc()).all())
    users = {u.id: (u.full_name or u.email) for u in db.query(User).filter(
        User.id.in_({r.user_id for r in rows if r.user_id})).all()} if rows else {}
    for r in rows:
        if r.job_id in out:
            continue
        out[r.job_id] = {"kind": r.kind, "outcome": r.outcome,
                         "outcome_label": sa_core.OUTCOMES.get(r.outcome or "", None),
                         "by": users.get(r.user_id), "at": _iso(r.created_at), "text": r.text}
    return out


def _decorate(db: Session, rows: list[dict]) -> list[dict]:
    jobs = {j.id: j for j in db.query(Job).filter(Job.id.in_([r["job_id"] for r in rows])).all()} if rows else {}
    known = _known(db)
    notes = _latest_notes(db, list(jobs))
    for r in rows:
        j = jobs.get(r["job_id"])
        key = sa_core.issue_key(j, r["kind"]) if j is not None else r["kind"]
        k = known.get(key)
        r["issue_key"] = key
        r["issue_title"] = (k.title if k and k.title else sa_core.default_title(key, j))
        r["known_fix"] = k.fix if k and k.fix else None
        r["last_note"] = notes.get(r["job_id"])
    return rows


# ---------------------------------------------------------------- home

@router.get("/home")
def home(db: Session = Depends(get_db), _u: User = Depends(_desk)) -> dict:
    from app.core.support_health import DOWN, OK, components

    today = datetime.now(desk.IST).date().isoformat()
    ov = desk.overview(today, today, db=db, _user=_u)
    health = components(db)
    problems = [{"key": b["key"], "label": b["label"], "status": b["status"], "detail": b["detail"]}
                for b in health["components"] if b["status"] != OK]
    issues = desk._issues(db)
    kinds = Counter(i["kind"] for i in issues)
    alerts = desk._recent_activity(db, 5, kind="alert")
    return {
        "health": {"status": health["status"], "problems": problems,
                   "down": sum(1 for p in problems if p["status"] == DOWN), "checked_at": health["checked_at"]},
        "today": {k: ov["tiles"][k] for k in ("total_jobs", "submitted", "failed", "delayed", "pages")},
        "stuck_in_ai": kinds.get("extraction_slow", 0) + kinds.get("extraction_failed", 0),
        "open_problems": len(issues),
        "open_by_kind": dict(kinds),
        "alerts": alerts,
    }


# ---------------------------------------------------------------- issues / job

@router.get("/issues")
def issues(include_handled: bool = True, db: Session = Depends(get_db), _u: User = Depends(_desk)) -> dict:
    rows = _decorate(db, desk._issues(db, include_handled=include_handled))
    open_rows = [r for r in rows if not r["handled"]]
    open_rows.sort(key=lambda r: r["since"] or "", reverse=True)
    done = [r for r in rows if r["handled"]]
    done.sort(key=lambda r: r["since"] or "", reverse=True)
    return {"open": len(open_rows), "items": open_rows + done}


def _timeline(j: Job) -> list[dict]:
    steps = [
        ("Arrived", j.created_at),
        ("AI reading started", j.extraction_started_at),
        ("AI reading finished", j.extraction_finished_at),
        ("First sent to ERP", j.erp_first_submitted_at),
        ("Last sent to ERP", j.erp_submitted_at if j.erp_submitted_at != j.erp_first_submitted_at else None),
        ("Entered in ERP", j.ai_submitted_at),
        ("ERP failed", j.erp_failed_at if desk._is_failed(j) else None),
    ]
    out = [{"label": label, "at": _iso(at)} for label, at in steps if at]
    out.sort(key=lambda s: s["at"])
    return out


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, db: Session = Depends(get_db), _u: User = Depends(_desk)) -> dict:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Job not found")
    issue = next((r for r in desk._issues(db, include_handled=True) if r["job_id"] == job_id), None)
    g = db.get(TemplateGroup, j.group_id) if j.group_id else None
    tenant = db.get(Tenant, j.tenant_id)
    kind = issue["kind"] if issue else ("erp_failed" if desk._is_failed(j) else None)
    key = sa_core.issue_key(j, kind) if kind else None
    k = db.query(SupportKnownIssue).filter(SupportKnownIssue.key == key).first() if key else None
    help_ = j.erp_help if isinstance(j.erp_help, dict) else {}
    corrections = [{"field": it.get("display") or it.get("field") or ", ".join(it.get("erp_columns") or []),
                    "needs": it.get("needs") or "", "example": it.get("example") or "",
                    "suggest": it.get("suggest") or "", "value": it.get("value") or ""}
                   for it in (help_.get("items") or [])][:20]
    events = (db.query(JobEvent).filter(JobEvent.job_id == j.id)
              .order_by(JobEvent.created_at.desc()).limit(15).all())
    from app.models.job_failure import JobFailureReport

    past = (db.query(JobFailureReport).filter(JobFailureReport.job_id == j.id)
            .order_by(JobFailureReport.created_at.desc()).limit(20).all())
    notes = desk._recent_activity(db, 50, job_id=j.id)
    outcomes = {a.id: a.outcome for a in db.query(SupportActivity).filter(
        SupportActivity.id.in_([n["id"] for n in notes])).all()} if notes else {}
    for n in notes:
        n["outcome"] = outcomes.get(n["id"])
        n["outcome_label"] = sa_core.OUTCOMES.get(n["outcome"] or "", None)
    return {
        "job_id": j.id, "reference": j.reference, "client": tenant.name if tenant else "",
        "type": (g.mode if g is not None else None) or "", "template": g.name if g is not None else "",
        "status": j.status, "gk2_status": j.gk2_status, "stage": desk.PIPELINE_LABEL.get(desk._pipeline_stage(j)),
        "problem": issue["kind"] if issue else None,
        "reason": issue["reason"] if issue else (desk._reason(j) if desk._is_failed(j) else None),
        "since": issue["since"] if issue else None, "age_minutes": issue["age_minutes"] if issue else None,
        "handled": bool(issue and issue["handled"]),
        "can_retry_erp": bool(issue and issue["can_retry_erp"]),
        "can_retry_extraction": bool(issue and issue["can_retry_extraction"]),
        "erp_message": (j.erp_reason or "")[:1000] or None,
        "corrections": corrections,
        "issue_key": key, "issue_title": (k.title if k and k.title else (sa_core.default_title(key, j) if key else None)),
        "known_fix": k.fix if k and k.fix else None,
        "timeline": _timeline(j),
        "events": [{"at": _iso(e.created_at), "status": e.status, "note": e.note} for e in events],
        "notes": notes,
        # R6: every ERP failure this job had, kept after a successful retry.
        "erp_failures": [{"at": _iso(r.created_at), "reason": (r.reason or "")[:400], "status": r.status} for r in past],
    }


class NoteIn(BaseModel):
    text: str = ""
    outcome: str | None = None


@router.post("/jobs/{job_id}/notes")
def add_note(job_id: str, p: NoteIn, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="Job not found")
    outcome = (p.outcome or "").strip() or None
    if outcome and outcome not in sa_core.OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of: {', '.join(sa_core.OUTCOMES)}")
    text = (p.text or "").strip()
    if not text and not outcome:
        raise HTTPException(status_code=422, detail="Write what you did, or pick a status")
    label = sa_core.OUTCOMES.get(outcome or "")
    row = SupportActivity(kind="note", tenant_id=j.tenant_id, job_id=j.id, user_id=user.id, outcome=outcome,
                          text=(f"[{label}] " if label else "") + (text or "")[:4000])
    db.add(row)
    db.commit()
    out = desk._activity_out(db, [row])[0]
    out["outcome"], out["outcome_label"] = outcome, label
    return out


# ---------------------------------------------------------------- known issues

@router.get("/known-issues")
def known_issues(db: Session = Depends(get_db), _u: User = Depends(_desk)) -> dict:
    rows = _decorate(db, desk._issues(db))
    groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "jobs": []})
    for r in rows:
        g = groups[r["issue_key"]]
        g["key"], g["title"], g["fix"] = r["issue_key"], r["issue_title"], r["known_fix"]
        g["count"] += 1
        if len(g["jobs"]) < 5:
            g["jobs"].append({"job_id": r["job_id"], "reference": r["reference"], "client": r["client"]})
    for k in db.query(SupportKnownIssue).all():
        if k.key not in groups:
            groups[k.key].update({"key": k.key, "title": k.title or sa_core.default_title(k.key), "fix": k.fix or None})
    items = sorted(groups.values(), key=lambda g: (-g["count"], g["title"] or ""))
    return {"items": items}


class KnownIn(BaseModel):
    title: str = ""
    fix: str = ""


@router.put("/known-issues/{key:path}")
def save_known(key: str, p: KnownIn, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    key = key.strip()[:160]
    if not key:
        raise HTTPException(status_code=422, detail="Missing key")
    k = db.query(SupportKnownIssue).filter(SupportKnownIssue.key == key).first()
    if k is None:
        k = SupportKnownIssue(key=key)
        db.add(k)
    k.title = (p.title or "").strip()[:200]
    k.fix = (p.fix or "").strip()[:4000]
    k.updated_by_id = user.id
    db.commit()
    return {"key": k.key, "title": k.title or sa_core.default_title(k.key), "fix": k.fix or None}


# ---------------------------------------------------------------- devices (push)

class DeviceIn(BaseModel):
    token: str
    platform: str = ""


@router.post("/devices")
def register_device(p: DeviceIn, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    token = (p.token or "").strip()
    if not token.startswith(("ExponentPushToken[", "ExpoPushToken[")) or len(token) > 200:
        raise HTTPException(status_code=422, detail="Not an Expo push token")
    d = db.query(SupportDevice).filter(SupportDevice.push_token == token).first()
    if d is None:
        d = SupportDevice(push_token=token, user_id=user.id)
        db.add(d)
    d.user_id = user.id
    d.platform = (p.platform or "")[:10]
    d.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


class DeviceOut(BaseModel):
    token: str


@router.post("/devices/remove")
def remove_device(p: DeviceOut, db: Session = Depends(get_db), user: User = Depends(_desk)) -> dict:
    db.query(SupportDevice).filter(SupportDevice.push_token == p.token,
                                   SupportDevice.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


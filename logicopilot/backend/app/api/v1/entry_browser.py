from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pathlib import Path

from app.api.v1.jobs import entry_uploads, entry_values_and_rows, persist_run_outcome
from app.core.browser import manager, playback_manager
from app.core.config import get_settings
from app.core.deps import get_db, require_role
from app.db.session import SessionLocal
from app.models.erp_script import ErpScript
from app.models.job import Job, JobFieldValue
from app.models.tenant import Tenant
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/entry-browser", tags=["entry browser"])


def _script_for_job(db: Session, job: Job) -> ErpScript:
    script = next(
        (
            s
            for s in db.query(ErpScript).filter(ErpScript.tenant_id == job.tenant_id, ErpScript.status == "ready").all()
            if job.group_id in (s.template_ids or [])
        ),
        None,
    )
    if script is None:
        raise HTTPException(status_code=422, detail="No ready ERP script for this template.")
    return script


class Point(BaseModel):
    x: float
    y: float


class TypeAt(BaseModel):
    x: float
    y: float
    value: str = ""


def _stepped_or_404(sid: str):
    s = manager.get(sid)
    if s is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or expired")
    return s


@router.get("/companies")
def companies(db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Companies (tenants) that have at least one ERP script."""
    tenant_ids = {s.tenant_id for s in db.query(ErpScript).all()}
    out = []
    for t in db.query(Tenant).all():
        if t.id in tenant_ids:
            out.append({"id": t.id, "name": t.name})
    return out


@router.get("/companies/{tenant_id}/templates")
def templates(tenant_id: str, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Templates for a company that have an ERP script, with their failed-job counts."""
    scripts = db.query(ErpScript).filter(ErpScript.tenant_id == tenant_id).all()
    gids: set[str] = set()
    for s in scripts:
        for g in s.template_ids or []:
            gids.add(g)
    out = []
    for gid in gids:
        g = db.get(TemplateGroup, gid)
        if g is None:
            continue
        failed = db.query(Job).filter(Job.group_id == gid, Job.status == "failed").count()
        total = db.query(Job).filter(Job.group_id == gid, Job.status.in_(("failed", "completed", "duplicate"))).count()
        out.append({"id": g.id, "name": g.name, "failed_count": failed, "job_count": total})
    out.sort(key=lambda x: (-x["failed_count"], -x["job_count"], x["name"]))
    return out


@router.get("/templates/{group_id}/failed-jobs")
def failed_jobs(group_id: str, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Jobs for a template that have been (or can be) run through the ERP — failed,
    completed and duplicate — so a Super Admin can re-run any of them. Failed first."""
    # `processing` is here so a run that is HAPPENING can be watched, which the docstring
    # always promised ("or can be") but the filter never allowed - the list came back empty
    # and there was no way to see an entry in flight at all. `extracted` is a job that is
    # ready to run but never has been; it is offered so a first entry can be driven stepwise
    # rather than only a repeat of one.
    jobs = (
        db.query(Job)
        .filter(Job.group_id == group_id,
                Job.status.in_(("processing", "failed", "duplicate", "completed", "extracted")))
        .order_by(Job.updated_at.desc())
        .all()
    )
    # Running first - that is the one somebody is waiting on.
    rank = {"processing": -1, "failed": 0, "duplicate": 1, "completed": 2, "extracted": 3}
    jobs.sort(key=lambda j: (rank.get(j.status, 3), -(j.updated_at.timestamp() if j.updated_at else 0)))
    out = []
    for j in jobs:
        oper = db.get(User, j.assigned_operator_id) if j.assigned_operator_id else None
        out.append(
            {
                "id": j.id,
                "reference": j.reference,
                "status": j.status,  # processing | failed | duplicate | completed | extracted
                # A run in flight must NOT be offered a rerun: starting one opens a second
                # browser and replays the script from the top, putting the same entry into
                # the ERP twice. Watch it instead.
                "live": j.status == "processing",
                "can_rerun": j.status != "processing",
                "operator": oper.full_name if oper else None,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            }
        )
    return out


@router.post("/jobs/{job_id}/rerun-live")
def rerun_live(job_id: str, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Start a live (streamed) rerun of a job's ERP entry — returns a playback session id."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == "processing":
        raise HTTPException(
            status_code=409,
            detail=("This job is being entered right now. Starting a rerun would open a second "
                    "browser and put the same entry into the ERP twice - watch the live run "
                    "instead."),
        )
    script = next(
        (
            s
            for s in db.query(ErpScript).filter(ErpScript.tenant_id == job.tenant_id, ErpScript.status == "ready").all()
            if job.group_id in (s.template_ids or [])
        ),
        None,
    )
    if script is None:
        raise HTTPException(status_code=422, detail="No ready ERP script for this template.")
    # The same inputs a real run gets, from the same code. This used to be one dict keyed on
    # label alone, with no files at all: fourteen product lines collapsed into one value, and
    # the Excel workbook was never built - so the import popup opened with "No file chosen",
    # stayed open, and every step after it failed against a dimmer it could not get past.
    values, rows = entry_values_and_rows(db, job)
    out_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        uploads = entry_uploads(db, job, values, rows, out_dir)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f"The import spreadsheet could not be built: {exc}") from exc
    login = (
        {"username": script.login_username or "", "password": script.login_password or ""}
        if script.has_login
        else None
    )
    # When the rerun finishes, write the job up exactly as Submit would. This used to do
    # nothing at all: the Entry Browser drove the whole entry, showed "Completed - 24 of 30
    # steps done" with the ERP's checklist rendered beside it, and the job still read "failed"
    # on the admin screen and on the operator's, with no log and no captured reference. The
    # run happened; nobody wrote it down.
    #
    # Its own Session, because this fires on the playback thread long after the request that
    # started it has gone and taken `db` with it.
    job_id_ = job.id
    script_id_ = script.id

    def _record(result: dict) -> None:
        own = SessionLocal()
        try:
            j = own.get(Job, job_id_)
            if j is None:
                return
            persist_run_outcome(own, j, own.get(ErpScript, script_id_), result,
                                operator_id=j.assigned_operator_id)
        finally:
            own.close()

    sid = playback_manager.start(script.url, login, script.steps or [], values,
                                 rows=rows, uploads=uploads, downloads_dir=out_dir,
                                 on_done=_record)
    return {"session_id": sid, "erp_url": script.url}


@router.post("/jobs/{job_id}/stepped-start")
def stepped_start(job_id: str, db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Open a persistent live browser for step-by-step rerun: you drive it with Next, and
    can click/type on the page yourself between steps."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status == "processing":
        raise HTTPException(
            status_code=409,
            detail=("This job is being entered right now. Starting a rerun would open a second "
                    "browser and put the same entry into the ERP twice - watch the live run "
                    "instead."),
        )
    script = _script_for_job(db, job)
    login = (
        {"username": script.login_username or "", "password": script.login_password or ""}
        if script.has_login
        else None
    )
    try:
        # Headless — the Entry Browser streams it into the app panel (no separate window).
        sid = manager.start(script.url, login, headless=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not open the ERP: {exc}")
    s = manager.get(sid)
    # The same files a real run gets. Without them the upload step has nothing to attach.
    values, rows = entry_values_and_rows(db, job)
    out_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        uploads = entry_uploads(db, job, values, rows, out_dir)
    except Exception:  # noqa: BLE001
        uploads = {}
    s.load_steps(script.steps or [], values, uploads=uploads)
    return {
        "session_id": sid,
        "erp_url": script.url,
        # Only count assigned elements (components); page loads / navigate / wait aren't steps.
        "total": getattr(s, "_total_components", 0),
        "screenshot": s.screenshot_b64(),
    }


@router.post("/stepped/{sid}/next")
def stepped_next(sid: str, _=Depends(require_role(SUPER_ADMIN))):
    """Run only the next recorded step."""
    return _stepped_or_404(sid).run_next()


@router.get("/stepped/{sid}/screenshot")
def stepped_screenshot(sid: str, _=Depends(require_role(SUPER_ADMIN))):
    return {"screenshot": _stepped_or_404(sid).screenshot_b64()}


@router.post("/stepped/{sid}/inspect")
def stepped_inspect(sid: str, p: Point, _=Depends(require_role(SUPER_ADMIN))):
    """Touch an element: focus it and return its descriptor so the UI can pop a value
    dialog for inputs/dropdowns (or just record a click for buttons)."""
    s = _stepped_or_404(sid)
    info = s.inspect_at(p.x, p.y)
    return {"element": info, "screenshot": s.screenshot_b64()}


def _sel(info) -> str | None:
    return info.get("selector") if isinstance(info, dict) else None


@router.post("/stepped/{sid}/click")
def stepped_click(sid: str, p: Point, _=Depends(require_role(SUPER_ADMIN))):
    s = _stepped_or_404(sid)
    info = s.click_at(p.x, p.y)
    # A manual entry counts as a step — matched to its recorded step so nothing is overwritten.
    res = s.note_manual(_sel(info))
    return {"element": info, **res}


@router.post("/stepped/{sid}/select")
def stepped_select(sid: str, body: TypeAt, _=Depends(require_role(SUPER_ADMIN))):
    s = _stepped_or_404(sid)
    info = s.select_at(body.x, body.y, body.value)
    res = s.note_manual(_sel(info))
    return {"element": info, **res}


@router.post("/stepped/{sid}/type")
def stepped_type(sid: str, body: TypeAt, _=Depends(require_role(SUPER_ADMIN))):
    s = _stepped_or_404(sid)
    info = s.type_at(body.x, body.y, body.value)
    res = s.note_manual(_sel(info))
    return {"element": info, **res}


@router.post("/stepped/{sid}/stop")
def stepped_stop(sid: str, _=Depends(require_role(SUPER_ADMIN))):
    manager.stop(sid)
    return {"stopped": True}


@router.get("/playback/{session_id}/diagnose")
def playback_diagnose(session_id: str, _=Depends(require_role(SUPER_ADMIN))):
    """Why is this run sitting where it is - the recorded step beside what is on screen NOW.

    A stuck run looks the same from outside whatever the cause: the element may be missing, or
    present but invisible, or present and covered by a dialog, or the page may simply not have
    finished loading. All four show the same screenshot and the same last log line, and each
    needs a different fix. Reading them off a screenshot is guesswork - this reports them.
    """
    st = playback_manager.get(session_id)
    if st is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Playback session not found")
    dbg = st.get("debug") or {}
    rec = dbg.get("recorded") or {}
    el = dbg.get("element") or {}
    page = dbg.get("page") or {}
    log = st.get("log") or []

    # Say it in words, so nobody has to interpret the numbers.
    if st.get("done"):
        verdict = f"finished - {st.get('status')}"
    elif not dbg:
        verdict = "no step reported yet - the run has not reached its first element"
    elif el.get("error"):
        verdict = f"could not look at the element: {el['error']}"
    elif not el:
        verdict = "this step has no element of its own (navigate / wait / switch_tab)"
    elif el.get("matches", 0) == 0:
        verdict = (f"{rec.get('selector')!r} is NOT on the page at all"
                   + (" - and this step is optional, so it will be skipped"
                      if rec.get("optional") else ""))
    elif not el.get("visible"):
        verdict = (f"{rec.get('selector')!r} exists ({el['matches']} match(es)) but none is "
                   "visible - it is hidden, or on a tab that is not open")
    elif el.get("covered_by"):
        verdict = (f"{rec.get('selector')!r} is visible but {el['covered_by']} is on top of it "
                   "- the ERP has something open over it")
    elif page.get("posting_back"):
        verdict = "the element is ready; the page is still posting back, so the run is waiting"
    elif page.get("ready_state") != "complete":
        verdict = f"the element is ready; the page is still loading ({page.get('ready_state')})"
    else:
        verdict = "the element is present, visible and clear - the step should be running"

    return {
        "verdict": verdict,
        "running": not st.get("done"),
        "step": dbg.get("step"),
        "of": dbg.get("of"),
        "action": dbg.get("action"),
        "recorded": rec,          # what the recording asked for
        "on_screen": el,          # what is actually there now
        "page": page,
        "log_tail": [str(x) for x in log[-6:]],
    }


@router.get("/playback/{session_id}")
def playback_status(session_id: str, _=Depends(require_role(SUPER_ADMIN))):
    """Poll a live rerun: latest screenshot + log + done/status."""
    st = playback_manager.get(session_id)
    if st is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playback session not found")
    res = st.get("result") or {}
    log = st.get("log") or []
    return {
        "screenshot": st.get("screenshot"),
        "log": log,
        "done": st.get("done"),
        "status": st.get("status"),
        "result": st.get("result"),
        # What the run is waiting for RIGHT NOW - the step, the page's state, and whether the
        # element is missing, invisible, or covered. Four situations that look identical in a
        # screenshot and need four different fixes.
        "debug": st.get("debug"),
        # A short summary of where it got to, so the panel can say it in words rather than
        # leaving somebody to count log lines.
        "summary": {
            "steps_done": res.get("steps_done", sum(1 for x in log if " ok" in str(x))),
            "steps_total": res.get("steps_total") or st.get("total") or 0,
            "stopped": bool(res.get("stopped")),
            "reason": res.get("reason"),
            "failed_steps": res.get("failed_steps") or [],
            "final_url": res.get("final_url"),
        },
    }


@router.post("/playback/{session_id}/stop")
def playback_stop(session_id: str, job_id: str | None = None,
                  db: Session = Depends(get_db), _=Depends(require_role(SUPER_ADMIN))):
    """Stop a live rerun that is under way, and mark its job failed.

    The flag is read between steps, so the browser closes at a clean point - killing it
    mid-action can leave half a value typed into an ERP field with no way to tell afterwards
    how much went in.
    """
    if not playback_manager.stop(session_id):
        raise HTTPException(status_code=409,
                            detail="That run has already finished - there is nothing to stop.")
    if job_id:
        job = db.get(Job, job_id)
        if job is not None and job.status == "processing":
            job.status = "failed"
            job.erp_status = "stopped"
            job.erp_reason = ("Stopped by hand from the Entry Browser. Check the ERP before "
                              "re-running - the steps already done were really done.")
            db.commit()
    return {"stopping": True,
            "note": "It will stop at the end of the step it is on."}

"""The large-job lane: big consolidation mails get their own worker, one at a time.

Why: a mail with a 10 MB zip (113 PDFs, 191 pages) took ~30 minutes to read and classify and
~55 more to extract (JOB-12B137, 2026-11-19). In the email cycle's shared pool that meant the
whole cycle - every other customer's mail - waited for it, and its classification and extraction
each held one of the six workers small jobs use (app/core/extraction_queue.py).

What this does, for a template with TemplateGroup.large_job_lane on:
  - The email cycle hands a mail whose biggest zip is over the template's large_zip_mb to this
    lane and carries on at once (email_puller._pull_inbox). The mail is not claimed yet, so if
    the server restarts before it starts, the next cycle simply finds it again.
  - Its classification runs right here on the lane's thread, not on the shared pool
    (extraction_queue.run_queued checks in_lane()).
  - "Run extraction" on such a job, pressed in ANY worker process, only records a marker on the
    job (attachment_notes["lane_extract"]); this lane - which lives in the one process that owns
    the singleton background work - picks it up within seconds. A marker left by a restart is
    picked up again by the next process (main._release_stranded_runs leaves such a job alone).
  - One large thing at a time: a second large mail or extraction waits its turn.

The lane's state (what runs, what waits) is written to uploads/large_job_lane/status.json so any
worker process can show it on Platform Settings.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MARKER = "lane_extract"
_POLL_SECONDS = 10.0

_lock = threading.Lock()
_status_lock = threading.Lock()   # one status write at a time (the cycle and the lane both write)
_wake = threading.Event()
_emails: deque[dict] = deque()
_queued_keys: set[str] = set()
_current: dict | None = None
_started = False
_tl = threading.local()


def in_lane() -> bool:
    """True on the lane's own worker thread."""
    return bool(getattr(_tl, "active", False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_path() -> Path:
    return Path(get_settings().uploads_dir) / "large_job_lane" / "status.json"


def _write_status() -> None:
    with _lock:
        data = {
            "updated_at": _now(),
            "current": dict(_current) if _current else None,
            "waiting_emails": [{"label": e["label"], "queued_at": e["queued_at"]} for e in _emails],
        }
    try:
        path = _status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _status_lock:
            tmp = path.with_name(f"status.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
    except Exception:  # noqa: BLE001 - status is for display only
        logger.exception("large-job lane: could not write status")


def read_status() -> dict:
    """For Platform Settings - works from any worker process."""
    try:
        data = json.loads(_status_path().read_text())
    except (OSError, ValueError):
        data = {"current": None, "waiting_emails": []}
    data["waiting_extractions"] = waiting_extractions()
    return data


def waiting_extractions() -> list[dict]:
    """Jobs whose Run extraction is waiting for (or running on) the lane."""
    try:
        from app.db.session import SessionLocal
        from app.models.job import Job

        db = SessionLocal()
        try:
            rows = db.query(Job.id, Job.reference, Job.attachment_notes).filter(Job.status == "extracting").all()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.exception("large-job lane: could not read waiting extractions")
        return []
    out = []
    for job_id, reference, notes in rows:
        req = (notes or {}).get(MARKER) if isinstance(notes, dict) else None
        if req:
            out.append({"job_id": job_id, "reference": reference, "requested_at": req.get("requested_at")})
    out.sort(key=lambda r: r.get("requested_at") or "")
    return out


def is_lane_job(job, group) -> bool:
    """A job whose extraction belongs on the lane: its template uses the lane and the job was
    read on the lane (attachment_notes["large_job"], set by email_puller only on the lane's
    thread - a Smart Upload, even of a big zip, never carries it)."""
    notes = job.attachment_notes if isinstance(job.attachment_notes, dict) else {}
    return bool(group is not None and getattr(group, "large_job_lane", False) is True and notes.get("large_job"))


def request_extraction(db, job, *, is_rerun: bool) -> None:
    """Called instead of the shared pool for a lane job's Run extraction (any worker process).
    The caller has already set status "extracting" and committed."""
    notes = dict(job.attachment_notes or {})
    notes[MARKER] = {"requested_at": _now(), "is_rerun": bool(is_rerun)}
    job.attachment_notes = notes
    db.commit()
    logger.info("job %s: extraction queued on the large-job lane", job.reference)
    _wake.set()


def submit_email(key: str, label: str, run: Callable[[], object]) -> bool:
    """Queue one harvested mail. False when that mail is already waiting or running (the next
    mail cycle finds the same unclaimed mail again - it must not be queued twice)."""
    with _lock:
        if key in _queued_keys:
            return False
        _queued_keys.add(key)
        _emails.append({"key": key, "label": label, "queued_at": _now(), "run": run})
    logger.info("large-job lane: queued mail %s (%s waiting)", label[:80], len(_emails))
    start()
    _wake.set()
    _write_status()
    return True


def _run_extraction(job_id: str) -> None:
    from app.api.v1.jobs import run_extraction
    from app.db.session import SessionLocal
    from app.models.job import Job

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        req = (job.attachment_notes or {}).get(MARKER) or {}
        try:
            run_extraction(db, job, is_rerun=bool(req.get("is_rerun")))
        except Exception:  # noqa: BLE001
            logger.exception("large-job lane: extraction failed for job %s", job_id)
            db.rollback()
            job = db.get(Job, job_id)
            if job is not None and job.status == "extracting":
                job.status = "draft"
                db.commit()
        job = db.get(Job, job_id)
        if job is not None and isinstance(job.attachment_notes, dict) and MARKER in job.attachment_notes:
            notes = dict(job.attachment_notes)
            notes.pop(MARKER, None)
            job.attachment_notes = notes
            db.commit()
    finally:
        db.close()


def _next_item() -> dict | None:
    with _lock:
        if _emails:
            return _emails.popleft()
    waiting = waiting_extractions()
    if waiting:
        w = waiting[0]
        return {"key": f"extract:{w['job_id']}", "label": f"Extraction {w['reference']}",
                "queued_at": w.get("requested_at"), "job_id": w["job_id"]}
    return None


def _loop() -> None:
    global _current
    time.sleep(5)
    while True:
        try:
            item = _next_item()
        except Exception:  # noqa: BLE001
            logger.exception("large-job lane: could not pick the next item")
            item = None
        if item is None:
            _wake.wait(_POLL_SECONDS)
            _wake.clear()
            continue
        with _lock:
            _current = {"label": item["label"], "started_at": _now()}
        _write_status()
        _tl.active = True
        started = time.monotonic()
        try:
            if "job_id" in item:
                _run_extraction(item["job_id"])
            else:
                item["run"]()
        except Exception:  # noqa: BLE001
            logger.exception("large-job lane: %s crashed", item["label"])
        finally:
            _tl.active = False
            with _lock:
                _current = None
                _queued_keys.discard(item["key"])
            _write_status()
            logger.info("large-job lane: %s finished in %.1f min", item["label"],
                        (time.monotonic() - started) / 60)


def start() -> None:
    """Start the lane's worker (idempotent). Called at startup in the process that owns the
    singleton background work, and on first use."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="large-job-lane").start()
    logger.info("large-job lane started")
    _write_status()

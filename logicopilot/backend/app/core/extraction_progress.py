"""Live progress of a job's extraction (Run / Re-run extraction), so the job page shows what is
happening instead of a static "AI is reading" line for minutes.

run_extraction reports each phase as it goes:
    text      reading each page's text            ("18 of 26 pages")
    ai        the AI reads, side by side (2d)      ("9 of 14 reads")
    docs      the per-document loop                ("document 7 of 10")
    finishing CTH lookups, computed fields, save   (no count)

Kept in memory, same reasoning as email_progress.py and live_runs.py: a frame is cheap, nobody
needs it once the job has moved on, and writing it to the database would be pure waste. The
endpoint only reports it while the job is still "extracting", so a run that failed and left a
frame behind never shows a stale bar.
"""
from __future__ import annotations

import threading
import time

STALE_AFTER = 30 * 60

_progress: dict[str, dict] = {}
_lock = threading.Lock()


def publish(job_id: str | None, phase: str, done: int = 0, total: int = 0) -> None:
    """Record where the job's extraction is. The ETA is projected from the time per unit
    actually seen so far in THIS phase."""
    if not job_id:
        return
    now = time.time()
    with _lock:
        prev = _progress.get(job_id) or {}
        started = prev.get("started") or now
        phase_started = prev["phase_started"] if prev.get("phase") == phase else now
        eta = None
        if total > 0 and 0 < done < total:
            eta = round((now - phase_started) / done * (total - done))
        _progress[job_id] = {"phase": phase, "done": int(done), "total": int(total),
                             "eta_seconds": eta, "started": started,
                             "phase_started": phase_started, "at": now}
        for jid, f in list(_progress.items()):
            if now - f["at"] > STALE_AFTER:
                del _progress[jid]


def latest(job_id: str) -> dict | None:
    with _lock:
        f = _progress.get(job_id)
        return dict(f) if f is not None else None


def clear(job_id: str | None) -> None:
    if not job_id:
        return
    with _lock:
        _progress.pop(job_id, None)

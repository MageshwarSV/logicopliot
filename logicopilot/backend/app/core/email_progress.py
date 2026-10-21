"""Live page-by-page OCR progress for an email-pulled job, while it still has no documents
in any slot - so the operator sees the job actually DOING something (N of M pages read, an
ETA) instead of it appearing to sit frozen on Document Capture for however long OCR and
classification take, which for a six-attachment email is several minutes.

Kept in memory, same reasoning as app/core/live_runs.py (which this mirrors): a frame is
cheap, nobody looks at it again once the job has moved on to having real documents, and
persisting it to the database would be pure waste for something this transient.
"""
import threading
import time

# job_id -> {"done": int, "total": int, "eta_seconds": int | None, "at": epoch seconds}
_progress: dict[str, dict] = {}
_lock = threading.Lock()

# Nothing removes a job's frame on its own once OCR finishes (see clear(), called the moment
# classification starts) - this is only a backstop against a crashed run leaving one behind
# forever. Generous on purpose: OCR across several multi-page attachments can genuinely take
# minutes.
STALE_AFTER = 1800.0


def publish(job_id: str, done: int, total: int, started_at: float) -> None:
    """Record how far OCR has got. `started_at` is this message's own time.time() from when
    page-counting began - used to project an ETA from the average time-per-page actually
    observed so far, not a guess fixed in advance (a scanned customs document and a native
    PDF take very different amounts of time)."""
    if not job_id or total <= 0:
        return
    now = time.time()
    eta_seconds = None
    if 0 < done < total:
        elapsed = now - started_at
        per_page = elapsed / done
        eta_seconds = round(per_page * (total - done))
    with _lock:
        _progress[job_id] = {"done": done, "total": total, "eta_seconds": eta_seconds, "at": now}
        for jid, f in list(_progress.items()):
            if now - f["at"] > STALE_AFTER:
                del _progress[jid]


def latest(job_id: str) -> dict | None:
    with _lock:
        f = _progress.get(job_id)
        return dict(f) if f is not None else None


def clear(job_id: str) -> None:
    with _lock:
        _progress.pop(job_id, None)

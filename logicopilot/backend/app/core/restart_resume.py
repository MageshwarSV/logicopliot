"""After a restart, carry on with the work the previous process was in the middle of - oldest first.

A deploy, a pm2 restart or a crash kills every extraction and every ERP run this process had in
flight or waiting: both live in threads and in-memory queues. Their jobs were left reading
"AI Processing" or "ERP Entry Process Started" with nothing behind them, and somebody had to
notice and push each one again by hand.

So at startup (in the one process that owns background work) this puts each of them back in
its queue, in the order it originally joined that queue (FIFO):

  - Extractions: every job still `extracting` - except a large-job-lane job, which the lane
    resumes on its own - is read again with the same settings it was started with (first read
    or re-run; one document or the whole job). Ordered by when its extraction began.

  - ERP: every job on the GK2/Final-Approve path still `preparing_erp` (queued, ERP not touched)
    or `entering_erp` (was inside the ERP) goes back to "Preparing for ERP" and is queued again,
    ordered by when it was submitted. By request (2026-11-20) this includes runs that were
    inside the ERP: the run starts again from the beginning.

The ERP backlog of the in-process pool exists only in memory, so the database is the record of
what was waiting. With Redis configured, its own queue survives the restart (requeue_orphans);
any job already sitting there is not queued a second time.
"""
import logging

logger = logging.getLogger(__name__)

ERP_IN_FLIGHT = ("preparing_erp", "entering_erp")


def _resume_extractions(db) -> list[str]:
    from sqlalchemy import func

    from app.api.v1.jobs import _start_extraction_background
    from app.models.job import Job
    from app.models.job_event import JobEvent

    jobs = [
        j for j in db.query(Job).filter(Job.status == "extracting").all()
        # The large-job lane resumes its own queue (app/core/large_job_lane.py).
        if not (isinstance(j.attachment_notes, dict) and j.attachment_notes.get("lane_extract"))
    ]
    if not jobs:
        return []
    began = dict(
        db.query(JobEvent.job_id, func.max(JobEvent.created_at))
        .filter(JobEvent.job_id.in_([j.id for j in jobs]), JobEvent.status == "extracting")
        .group_by(JobEvent.job_id)
        .all()
    )
    jobs.sort(key=lambda j: began.get(j.id) or j.created_at)
    resumed = []
    for j in jobs:
        notes = j.attachment_notes if isinstance(j.attachment_notes, dict) else {}
        _start_extraction_background(
            j.id, only_job_document_id=notes.get("extract_doc") or None,
            is_rerun=bool(j.extraction_is_rerun))
        resumed.append(j.reference or j.id)
    return resumed


def _already_in_redis() -> set[str]:
    import json

    from app.core import erp_redis_queue as q

    client = q._redis()
    if client is None:
        return set()
    out: set[str] = set()
    try:
        for key in (q.QUEUE_KEY, q.PROCESSING_KEY):
            for raw in client.lrange(key, 0, -1):
                try:
                    out.add(json.loads(raw).get("job_id"))
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        logger.warning("could not read the Redis ERP queue", exc_info=True)
    return out


def _resume_erp_runs(db) -> list[str]:
    from app.core.erp_redis_queue import enqueue
    from app.core.system_settings import get_erp_script_workers
    from app.models.job import Job
    from app.models.template_group import TemplateGroup

    jobs = db.query(Job).filter(Job.gk2_status.in_(ERP_IN_FLIGHT)).all()
    if not jobs:
        return []
    jobs.sort(key=lambda j: j.erp_submitted_at or j.updated_at or j.created_at)
    for j in jobs:
        # _run_gk2_submission only acts on "preparing_erp" - and that is what the screen
        # should say while it waits its turn again.
        j.gk2_status = "preparing_erp"
    db.commit()

    skip = _already_in_redis()
    workers = get_erp_script_workers(db)
    resumed = []
    for j in jobs:
        if j.id in skip:
            continue
        grp = db.get(TemplateGroup, j.group_id) if j.group_id else None
        is_excel_entry = grp is not None and (grp.entry_mode or "fields") == "excel"
        enqueue(j.id, is_excel_entry=is_excel_entry, desired_workers=workers)
        resumed.append(j.reference or j.id)
    return resumed


def resume_interrupted_work(kinds: tuple[str, ...] = ("extraction", "ERP")) -> None:
    """kinds: which side to resume - both by default (one process does everything); a split
    server passes only its own (app/core/process_role.py)."""
    from app.db.session import SessionLocal

    for name, fn in (("extraction", _resume_extractions), ("ERP", _resume_erp_runs)):
        if name not in kinds:
            continue
        db = SessionLocal()
        try:
            resumed = fn(db)
            if resumed:
                logger.warning("restart: re-queued %d interrupted %s run(s), oldest first: %s",
                               len(resumed), name, ", ".join(resumed))
        except Exception:  # noqa: BLE001 - one side failing must not stop the other
            logger.exception("restart: could not resume interrupted %s runs", name)
            db.rollback()
        finally:
            db.close()

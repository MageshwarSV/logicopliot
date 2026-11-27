"""Production Support S2 - Application Health: one green / amber / red box per part of the
system, read from what already exists.

  Servers      - the heartbeat every process already writes (queue_status.py, every 30 s,
                 kept 120 s). A split setup expects web, worker and erp; a missing one is down.
  Database     - a SELECT 1 and how long it took.
  Redis/queues - queue_status.snapshot(): waiting / running / oldest wait, stuck warnings.
  Email puller - mail polling on in some worker heartbeat; mailboxes paused; AI-unavailable hold.
  AI service   - the mail process's AI-unavailable hold, and when the last extraction finished.
  ERP logins   - erp_branch_sessions: enabled logins and how many are live.
  Uploads disk - free space where documents are stored.

Nothing here changes anything, and every check is wrapped: one failing check is shown as
"unknown", it never breaks the page.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

OK, WARN, DOWN, UNKNOWN = "ok", "warn", "down", "unknown"


def _box(key: str, label: str, status: str, detail: str) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _safe(fn, key: str, label: str) -> dict:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("support health check %s failed: %s", key, exc)
        return _box(key, label, UNKNOWN, "Could not check")


def components(db: Session) -> dict:
    from app.core.process_role import is_split
    from app.core.queue_status import snapshot

    try:
        snap = snapshot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("queue snapshot failed: %s", exc)
        snap = {"redis": False, "queues": [], "servers": [], "warnings": []}
    servers = snap.get("servers") or []
    boxes: list[dict] = []

    def servers_box() -> dict:
        roles = {s.get("role") for s in servers if (s.get("seen_seconds_ago") or 0) <= 120}
        if not snap.get("redis"):
            return _box("servers", "Servers", UNKNOWN, "No Redis - server heartbeats are not available")
        expected = ["web", "worker", "erp"] if (is_split() or roles - {"all"}) else ["all"]
        missing = [r for r in expected if r not in roles and not (r != "all" and "all" in roles)]
        names = ", ".join(sorted({f"{s.get('host')} ({s.get('role')})" for s in servers})) or "none"
        if missing:
            return _box("servers", "Servers", DOWN, f"Not reporting: {', '.join(missing)}. Seen: {names}")
        return _box("servers", "Servers", OK, f"{len(servers)} process(es) reporting: {names}")

    def db_box() -> dict:
        t = time.perf_counter()
        db.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - t) * 1000)
        return _box("database", "Database", OK if ms < 500 else WARN, f"Answered in {ms} ms")

    def queues_box() -> dict:
        if not snap.get("redis"):
            return _box("queues", "Redis / Queues", WARN, "Redis not configured - queues run in memory")
        qs = snap.get("queues") or []
        summary = "; ".join(f"{q['label']}: {q['waiting']} waiting, {q['running']} running" for q in qs)
        warnings = [w for w in (snap.get("warnings") or []) if "AI service" not in w]
        if warnings:
            return _box("queues", "Redis / Queues", WARN, " | ".join(warnings))
        return _box("queues", "Redis / Queues", OK, summary or "Empty")

    def mail_box() -> dict:
        from app.models.connected_mailbox import ConnectedMailbox

        total = db.query(ConnectedMailbox).count()
        paused = db.query(ConnectedMailbox).filter(ConnectedMailbox.paused.is_(True)).count()
        polling = any(s.get("mail_polling") for s in servers) if servers else None
        detail = f"{total - paused} of {total} mailbox(es) active"
        if paused:
            detail += f", {paused} paused"
        if polling is False:
            return _box("email", "Email Puller", WARN, detail + " - mail polling is off on every server")
        return _box("email", "Email Puller", OK if not paused or total > paused else WARN, detail)

    def ai_box() -> dict:
        from app.models.job import Job

        held = [s.get("ai_wait") for s in servers if s.get("ai_wait")]
        last = (db.query(Job.extraction_finished_at).filter(Job.extraction_finished_at.isnot(None))
                .order_by(Job.extraction_finished_at.desc()).first())
        last_txt = ""
        if last and last[0]:
            at = last[0] if last[0].tzinfo else last[0].replace(tzinfo=timezone.utc)
            mins = int((datetime.now(timezone.utc) - at).total_seconds() // 60)
            last_txt = f"Last extraction finished {mins} min ago"
        if held:
            reason = str(held[0].get("reason") or "")[:120]
            return _box("ai", "AI / OCR Services", DOWN, f"AI unavailable - mail reading paused. {reason}".strip())
        return _box("ai", "AI / OCR Services", OK, last_txt or "No extraction finished yet since this was added")

    def erp_box() -> dict:
        from app.models.erp_branch_session import ErpBranchSession

        rows = db.query(ErpBranchSession).filter(ErpBranchSession.enabled.is_(True)).all()
        if not rows:
            return _box("erp", "ERP Automation", OK, "No branch logins set up")
        live = [r for r in rows if (r.status or "") == "live"]
        detail = f"{len(live)} of {len(rows)} branch login(s) live"
        bad = [f"{r.branch}: {r.status}" for r in rows if (r.status or "") != "live"]
        if bad:
            detail += " - " + ", ".join(bad[:6])
        if not live:
            return _box("erp", "ERP Automation", DOWN, detail)
        return _box("erp", "ERP Automation", OK if len(live) == len(rows) else WARN, detail)

    def disk_box() -> dict:
        from app.core.config import get_settings

        u = shutil.disk_usage(get_settings().uploads_dir or ".")
        free_pct = round(100 * u.free / u.total, 1)
        detail = f"{u.free // (1024 ** 3)} GB free ({free_pct}%)"
        return _box("disk", "Storage (uploads)", OK if free_pct >= 15 else (WARN if free_pct >= 5 else DOWN), detail)

    def backup_box() -> dict:
        """The nightly S3 backup (deploy/backup/cargora-db-backup.sh) writes
        cargora:backup:<env> to Redis after every run."""
        import json as _json

        from app.core.erp_redis_queue import _redis

        client = _redis()
        if client is None:
            return _box("backup", "Database backup", UNKNOWN, "No Redis - cannot read the backup status")
        found = []
        for key in client.scan_iter(match="cargora:backup:*", count=50):
            try:
                found.append((key.decode() if isinstance(key, bytes) else key, _json.loads(client.get(key) or "{}")))
            except Exception:  # noqa: BLE001
                continue
        if not found:
            return _box("backup", "Database backup", WARN, "No backup has reported yet")
        key, rec = sorted(found, key=lambda kv: kv[1].get("at") or "")[-1]
        at = datetime.fromisoformat(rec["at"]) if rec.get("at") else None
        hours = (datetime.now(timezone.utc) - at).total_seconds() / 3600 if at else None
        when = f"{hours:.0f} h ago" if hours is not None else "time unknown"
        if rec.get("status") != "ok":
            return _box("backup", "Database backup", DOWN, f"Last backup FAILED ({when}): {rec.get('detail') or ''}")
        if hours is None or hours > 26:
            return _box("backup", "Database backup", DOWN, f"No successful backup for {when} - last: {rec.get('detail') or ''}")
        return _box("backup", "Database backup", OK, f"Last backup {when}: {rec.get('detail') or ''}")

    boxes.append(_box("web", "Web Application", OK, "Answering (this page loaded)"))
    for fn, key, label in ((servers_box, "servers", "Servers"), (db_box, "database", "Database"),
                           (queues_box, "queues", "Redis / Queues"), (mail_box, "email", "Email Puller"),
                           (ai_box, "ai", "AI / OCR Services"), (erp_box, "erp", "ERP Automation"),
                           (disk_box, "disk", "Storage (uploads)"), (backup_box, "backup", "Database backup")):
        boxes.append(_safe(fn, key, label))
    worst = DOWN if any(b["status"] == DOWN for b in boxes) else (
        WARN if any(b["status"] in (WARN, UNKNOWN) for b in boxes) else OK)
    return {"status": worst, "components": boxes, "queues": snap.get("queues") or [],
            "servers": servers, "checked_at": datetime.now(timezone.utc).isoformat()}

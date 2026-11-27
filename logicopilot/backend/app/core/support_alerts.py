"""Production Support S5 - alerts by email (WhatsApp left out by request, 2026-11-27).

Every CHECK_EVERY seconds ONE process (a Redis lock, or a file lock without Redis - the
website runs several workers) looks for:

  - a health box that is down (a server not reporting, ERP logins all down, AI unavailable,
    disk nearly full) - support_health.components();
  - a stuck queue (queue_status warnings);
  - new failed jobs since the last check;
  - extractions running past the SLA.

Each alert is written to the support activity log (kind "alert", shown on the support screen)
and emailed to the addresses saved on Application Health, through the SMTP_* settings in
.env. The same alert (alert_key) is not repeated within REPEAT_AFTER. Without SMTP settings
the alert is still logged - only the email is skipped.
"""
from __future__ import annotations

import fcntl
import logging
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CHECK_EVERY = 300
REPEAT_AFTER = timedelta(minutes=60)
LOCK_KEY = "support:alerts:lock"

DEFAULT_SLA = {"email_to_ai": 2, "extraction": 4, "queue_wait": 3, "erp": 5}

_started = False
_guard = threading.Lock()


# ---------------------------------------------------------------- settings

def get_config(db: Session) -> dict:
    from app.core.system_settings import get_system_settings

    raw = get_system_settings(db).support_config or {}
    sla = {**DEFAULT_SLA, **{k: v for k, v in (raw.get("sla") or {}).items() if k in DEFAULT_SLA}}
    return {"sla": sla, "alert_emails": list(raw.get("alert_emails") or [])}


def set_config(db: Session, sla: dict | None, alert_emails: list[str] | None) -> dict:
    from app.core.system_settings import get_system_settings

    row = get_system_settings(db)
    cur = get_config(db)
    if sla is not None:
        cur["sla"] = {k: max(1, int(sla.get(k, cur["sla"][k]))) for k in DEFAULT_SLA}
    if alert_emails is not None:
        cur["alert_emails"] = sorted({e.strip().lower() for e in alert_emails if e and "@" in e})[:20]
    row.support_config = cur
    db.commit()
    return cur


def email_configured() -> bool:
    from app.core.config import get_settings

    s = get_settings()
    return bool(s.smtp_host and (s.smtp_from or s.smtp_user))


def send_email(to: list[str], subject: str, body: str) -> None:
    """Raises on failure, so the caller can say why."""
    from app.core.config import get_settings

    s = get_settings()
    if not email_configured():
        raise RuntimeError("Email is not set up on the server (SMTP_HOST / SMTP_USER / SMTP_PASSWORD in .env)")
    if not to:
        raise RuntimeError("No alert email addresses saved")
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if int(s.smtp_port) == 465:
        with smtplib.SMTP_SSL(s.smtp_host, 465, context=ssl.create_default_context(), timeout=20) as smtp:
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, int(s.smtp_port), timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)


# ---------------------------------------------------------------- checks

def _recent(db: Session, key: str) -> bool:
    from app.models.support_activity import SupportActivity

    since = datetime.now(timezone.utc) - REPEAT_AFTER
    return db.query(SupportActivity).filter(SupportActivity.alert_key == key,
                                            SupportActivity.created_at >= since).first() is not None


def find_alerts(db: Session, since: datetime) -> list[tuple[str, str]]:
    """[(alert_key, text)] for what is wrong right now."""
    from app.core.support_health import DOWN, components
    from app.models.job import Job

    out: list[tuple[str, str]] = []
    health = components(db)
    for b in health["components"]:
        if b["status"] == DOWN:
            out.append((f"down:{b['key']}", f"{b['label']} is DOWN - {b['detail']}"))
    for q in health.get("queues") or []:
        if q.get("stale"):
            out.append((f"queue:{q['name']}", f"{q['label']} queue: the oldest item has waited "
                                              f"{(q.get('oldest_wait_seconds') or 0) // 60} min"))
    new_failed = (db.query(Job).filter(Job.erp_failed_at.isnot(None), Job.erp_failed_at >= since,
                                       (Job.gk2_status == "failed") | (Job.status == "failed")).all())
    if new_failed:
        refs = ", ".join(j.reference for j in new_failed[:10])
        key = "failed:" + ",".join(sorted(j.reference for j in new_failed))
        out.append((key[:120], f"{len(new_failed)} new failed job(s): {refs}"))
    sla = get_config(db)["sla"]
    from app.api.v1.support import _issues

    slow = [i for i in _issues(db) if i["kind"] in ("extraction_slow", "erp_slow")]
    if slow:
        refs = ", ".join(i["reference"] for i in slow[:10])
        out.append(("slow:" + ",".join(sorted(i["reference"] for i in slow))[:100],
                    f"{len(slow)} job(s) past the SLA (extraction {sla['extraction']} min / ERP {sla['erp']} min): {refs}"))
    return out


def check_once(db: Session, since: datetime) -> list[str]:
    """Raise and email what is new. Returns the alert texts raised."""
    from app.models.support_activity import SupportActivity

    raised: list[tuple[str, str]] = []
    for key, txt in find_alerts(db, since):
        if _recent(db, key):
            continue
        db.add(SupportActivity(kind="alert", text=txt[:2000], alert_key=key[:120]))
        raised.append((key, txt))
    db.commit()
    if raised:
        to = get_config(db)["alert_emails"]
        if to and email_configured():
            body = "\n\n".join(t for _k, t in raised)
            body += "\n\nOpen Production Support in Cargora to investigate."
            try:
                send_email(to, f"Cargora alert: {raised[0][1][:80]}", body)
            except Exception as exc:  # noqa: BLE001 - the alert stays logged on screen
                logger.warning("support alert email failed: %s", exc)
    return [t for _k, t in raised]


# ---------------------------------------------------------------- loop

def _take_lock() -> bool:
    """One process sends. Redis lock first; a local file lock without Redis."""
    try:
        from app.core.erp_redis_queue import _redis

        client = _redis()
        if client is not None:
            return bool(client.set(LOCK_KEY, f"{os.getpid()}", nx=True, ex=CHECK_EVERY - 30))
    except Exception:  # noqa: BLE001
        pass
    global _lock_file
    try:
        if _lock_file is None:
            from app.core.config import get_settings

            _lock_file = open(os.path.join(get_settings().uploads_dir or ".", ".support_alerts.lock"), "a")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


_lock_file = None


def start_alert_loop() -> None:
    """main.py, website / all-in-one processes. Idempotent; never raises."""
    global _started
    from app.core.process_role import role

    with _guard:
        if _started or role() not in ("all", "web"):
            return
        _started = True

    def _loop() -> None:
        from app.db.session import SessionLocal

        last = datetime.now(timezone.utc)
        time.sleep(60)
        while True:
            now = datetime.now(timezone.utc)
            try:
                if _take_lock():
                    db = SessionLocal()
                    try:
                        check_once(db, last)
                    finally:
                        db.close()
            except Exception:  # noqa: BLE001 - alerts must never hurt the app
                logger.exception("support alert check failed")
            last = now
            time.sleep(CHECK_EVERY)

    threading.Thread(target=_loop, daemon=True, name="support-alerts").start()

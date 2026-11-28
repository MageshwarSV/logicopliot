"""Support mobile app (2026-11-28) - known-issue keys and push alerts to support phones.

issue_key(job, kind)
    Groups open problems that have the same cause, so the fix the desk writes once
    (SupportKnownIssue) shows on every job with that cause:

      erp_field:<field>      the ERP refused one field (first item of the rejection card)
      erp_login              the ERP login / session failed
      erp_invalid_data       the ERP said "Invalid Data Found" with no field we could name
      erp_timeout            the ERP did not answer in time
      erp_other              any other ERP failure
      erp_slow / extraction_slow / extraction_failed / missing_documents   (support._issues kinds)

send_push(db, title, body, data)
    Every active support user's registered phone, through the Expo push service
    (https://exp.host). Only the alert's short text and a job id go out - no documents, no
    customer data beyond the job reference already in the alert. Never raises.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
OUTCOMES = {
    "investigating": "Investigating",
    "fixed": "Fixed",
    "customer_informed": "Customer informed",
    "erp_side": "ERP side issue",
    "not_our_issue": "Not our issue",
}
KIND_TITLE = {
    "erp_login": "ERP login failed",
    "erp_invalid_data": "ERP said 'Invalid Data Found'",
    "erp_timeout": "ERP did not answer in time",
    "erp_other": "ERP entry failed (other reason)",
    "erp_slow": "ERP entry running too long",
    "extraction_slow": "AI reading taking too long",
    "extraction_failed": "AI reading did not finish",
    "missing_documents": "Waiting for required documents",
}


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()[:120]


def issue_key(job, kind: str) -> str:
    if kind != "erp_failed":
        return kind
    help_ = job.erp_help if isinstance(job.erp_help, dict) else {}
    items = help_.get("items") or []
    if items:
        first = items[0]
        name = _norm(first.get("display") or first.get("field") or ", ".join(first.get("erp_columns") or []))
        if name:
            return f"erp_field:{name}"
    text = " ".join(filter(None, [job.erp_reason, job.erp_diagnosis])).lower()
    if any(w in text for w in ("login", "password", "session", "logged out", "sign in")):
        return "erp_login"
    if "invalid data" in text:
        return "erp_invalid_data"
    if any(w in text for w in ("timeout", "timed out", "time out")):
        return "erp_timeout"
    return "erp_other"


def default_title(key: str, job=None) -> str:
    if key.startswith("erp_field:"):
        name = None
        if job is not None and isinstance(job.erp_help, dict) and job.erp_help.get("items"):
            name = job.erp_help["items"][0].get("display")
        return f"ERP refused {name or key.split(':', 1)[1].title()}"
    return KIND_TITLE.get(key, key)


# ---------------------------------------------------------------- push

def _tokens(db: Session) -> list[str]:
    from app.models.support_app import SupportDevice
    from app.models.user import SUPPORT, User

    rows = (db.query(SupportDevice.push_token)
            .join(User, User.id == SupportDevice.user_id)
            .filter(User.role == SUPPORT, User.is_active.is_(True)).all())
    return [r[0] for r in rows]


def _post(messages: list[dict]) -> list[dict]:
    req = urllib.request.Request(
        EXPO_PUSH_URL, data=json.dumps(messages).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https URL
        return (json.loads(resp.read() or b"{}").get("data")) or []


def send_push(db: Session, title: str, body: str, data: dict | None = None) -> int:
    """Returns how many phones it was sent to. Phones Expo says are gone are forgotten."""
    from app.models.support_app import SupportDevice

    try:
        tokens = _tokens(db)
    except Exception:  # noqa: BLE001
        logger.exception("support push: could not read devices")
        return 0
    sent, gone = 0, []
    for i in range(0, len(tokens), 100):
        batch = tokens[i:i + 100]
        msgs = [{"to": t, "title": title[:100], "body": body[:240], "data": data or {},
                 "sound": "default", "priority": "high", "channelId": "alerts"} for t in batch]
        try:
            for tok, ticket in zip(batch, _post(msgs)):
                if ticket.get("status") == "ok":
                    sent += 1
                elif (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                    gone.append(tok)
        except Exception as exc:  # noqa: BLE001 - alerts must never hurt the app
            logger.warning("support push failed: %s", exc)
    if gone:
        db.query(SupportDevice).filter(SupportDevice.push_token.in_(gone)).delete(synchronize_session=False)
        db.commit()
    return sent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

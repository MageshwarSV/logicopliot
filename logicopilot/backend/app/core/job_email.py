"""The "Prealert": the original email a job was created from, exactly as it arrived, kept for
GK1's IRN Documents Upload screen to show alongside the classified, split, per-slot copies the
rest of the app works from. Those copies are what extraction reads and an operator corrects -
this is the unedited source, useful when something about the classification is in question.

Saved once, right after the job it belongs to is created and committed
(app/core/email_puller.py) - the pipeline's own temporary working copy is deleted the moment
that job finishes processing, so this is the only chance to keep anything at all. A job created
any other way (Excel entry, a manual upload) has no email behind it and nothing is ever written
here for it - a missing directory below just means "not applicable", not "lost".
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMAIL_DIR = "job_email"


def job_email_dir(job_id: str) -> Path:
    return Path(get_settings().uploads_dir) / EMAIL_DIR / job_id


def original_attachments(job_id: str) -> list[tuple[str, bytes]]:
    """The attachments EXACTLY as they arrived, for a job created from an email.

    Empty list when this job has no email behind it (Excel entry, a manual upload) or the
    copy could not be read - the caller falls back to whatever it would have used before.

    This exists for reclassify. Re-running classification over a job's own PLACED documents
    cannot work for a combined attachment: one "Commercial Invoice & Packing List" PDF
    legitimately fills two slots, so each slot holds a copy of it, and feeding the slots back
    in hands the classifier the same document twice - under the same original name. They then
    merge into one document holding every invoice twice (stored as "<name> +1 more"), and
    every further reclassify compounds it. Seen on production JOB-CB9B46.
    """
    import json as _json

    try:
        out = job_email_dir(job_id)
        meta_path = out / "meta.json"
        att_dir = out / "attachments"
        if not meta_path.exists() or not att_dir.is_dir():
            return []
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        files: list[tuple[str, bytes]] = []
        for att in meta.get("attachments") or []:
            stored = att.get("stored_as")
            if not stored:
                continue
            # Never follow a name out of this job's own folder.
            path = (att_dir / stored).resolve()
            if not path.is_file() or att_dir.resolve() not in path.parents:
                continue
            files.append((att.get("name") or stored, path.read_bytes()))
        return files
    except Exception:  # noqa: BLE001 - a missing/odd copy must only cost the fallback
        logger.exception("could not read the original attachments for job %s", job_id)
        return []


def save_original_email(job_id: str, msg, files: list[tuple[str, bytes]],
                        sender: str, subject: str, received_by: str | None = None) -> None:
    """Keep the raw message (when there is one) and its as-received attachments for this job.
    Best-effort: a failure here must never take down the job it is trying to preserve a copy
    for.

    `msg` is None for a job resolved by hand from the pending-review queue - only the
    attachments survive there (app/api/v1/pending_mail.py never keeps the raw MIME message),
    which is still worth having: an operator reviewing IRN documents mainly wants to see what
    was actually attached, not the message envelope around it.

    `received_by` is the mailbox address that was being polled when this message was found -
    the shared inbox (e.g. "cargora@4slogistics.com") or one operator's own connected
    mailbox - see _pull_one_mailbox's own call site. This is what the Jobs list's "Assigned
    To" hint is actually built from now (see _pulled_from_sender in api/v1/jobs.py) - which
    account RECEIVED this job's email, not who sent it; the sender is an external party with
    no account here at all, while the receiving mailbox is always one of this tenant's own.
    """
    try:
        out = job_email_dir(job_id)
        att_dir = out / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        if msg is not None:
            (out / "original.eml").write_bytes(msg.as_bytes())
        attachments = []
        for idx, (name, blob) in enumerate(files):
            safe = "".join(ch for ch in (name or "attachment") if ch.isalnum() or ch in "._- ")
            safe = safe.strip() or f"attachment_{idx}"
            stored_as = f"{idx}_{safe}"[:200]
            (att_dir / stored_as).write_bytes(blob)
            attachments.append({"name": name, "stored_as": stored_as, "size": len(blob)})
        meta = {
            "sender": sender, "subject": subject, "received_by": received_by,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "attachments": attachments,
        }
        (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    except Exception:  # noqa: BLE001
        # Deliberately broad: this runs right after the job it is a nice-to-have for has
        # already been committed, and a malformed attachment name or a message.as_bytes()
        # oddity must never look like the job itself failed to process.
        logger.exception("could not keep a copy of the original email for job %s", job_id)


def has_original_eml(job_id: str) -> bool:
    return (job_email_dir(job_id) / "original.eml").exists()


def read_email_meta(job_id: str) -> dict | None:
    """The prealert's metadata for this job, or None if it was never created from an email
    (or predates this feature)."""
    path = job_email_dir(job_id) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

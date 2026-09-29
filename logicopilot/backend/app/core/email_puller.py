"""Email auto-pull: read a mailbox, work out whose documents these are, and make the job.

THE DOCUMENTS DECIDE WHICH CUSTOMER, not the address they came from. That is the whole point:
a forwarding agent mails for six importers from one address, a customer mails from whatever
laptop is to hand, and a new customer's very first email arrives before anyone has configured
an address for them. A Bill of Lading, though, names its consignee.

One message, start to finish:

  1. fetch it WITHOUT marking it read, and skip it entirely if we have examined it before
  2. pull out the attachments (including the contents of a .zip)
  3. render every page and OCR it - Document AI, vision fallback
  4. put the mail body and that text to the model: which of our importers is this?
        nothing recognised -> throw the extracted text and files away, write down why, move on.
                              The mail is left UNREAD so it can be pulled again once that
                              customer is configured.
        more than one      -> prefer the Excel-entry template; still tied, park it as ambiguous
  5. create the job, name each document from its own data (BL_1075599313.pdf, not attachment1),
     and route it into its slot with the same classifier the operator's smart-upload uses
  6. run extraction, and mark THIS message read - the only case where that happens

Every outcome is written to `email_seen`, keyed on Message-ID. OCR and two model calls per
message is real money, and a newsletter that matches nobody would otherwise pay it on every
poll for as long as it sits in the mailbox.
"""

import email
import imaplib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import time
from email.header import decode_header
from email.utils import getaddresses, parseaddr
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from sqlalchemy.exc import IntegrityError

from app.models.email_seen import EmailSeen
from app.models.job import Job, JobDocument
from app.models.pending_email import PendingEmail
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

_scheduler_started = False

CLASSIFY_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}
ATTACH_EXTS = CLASSIFY_EXTS | {".zip"}

# What the background poller's own last cycle did - not a message-processing result, a report
# on the CYCLE itself: how many mailboxes were there to check, how many of them actually got
# checked, and whether this tick ran at all or was skipped because the previous one was still
# busy. Read by GET /email/poll-status; written only by _record_cycle_status below.
#
# Persisted to system_settings.last_email_cycle_status (a JSON blob), not kept only in this
# process's memory: with uvicorn --workers N, only the single worker holding the lock in
# app/core/worker_lock.py ever runs the poll loop, but a GET to /email/poll-status can land on
# any of the N worker processes - an in-memory-only dict would read back empty on the other
# N-1 of them even while polling genuinely is happening.
import json

from sqlalchemy.orm import Session as _Session


def _record_cycle_status(res: dict, started_at: datetime, db: "_Session") -> None:
    from app.core.system_settings import get_system_settings

    payload = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        # True means the previous cycle had not finished yet, so THIS tick did nothing at
        # all - every connected mailbox is still exactly as checked (or not) as it was
        # before this tick, and will only be looked at once a cycle actually runs.
        "skipped_busy": bool(res.get("busy")),
        "mailboxes_total": res.get("mailboxes_total", 0),
        "mailboxes_ok": res.get("mailboxes_ok", 0),
        "mailboxes_failed": len(res.get("mailbox_errors") or []),
        "mailbox_errors": res.get("mailbox_errors") or [],
        "jobs_created": sum(1 for m in (res.get("processed") or []) if m.get("job_id")),
        "messages_processed": len(res.get("processed") or []),
    }
    settings_row = get_system_settings(db)
    settings_row.last_email_cycle_status = json.dumps(payload)
    db.commit()


def get_last_cycle_status(db: "_Session") -> dict:
    """A snapshot of what the background poller's most recent tick actually did - never
    triggers a pull itself, purely reads the last one's own report. Empty ({}) before the
    poller's very first tick has completed."""
    from app.core.system_settings import get_system_settings

    raw = get_system_settings(db).last_email_cycle_status
    return json.loads(raw) if raw else {}


def start_scheduler() -> bool:
    """Start the background inbox poller if enabled and any mailbox is configured — the
    tenant's shared inbox, or at least one operator's own. Idempotent — calling it more than
    once is a no-op. Returns True if it started."""
    global _scheduler_started
    settings = get_settings()
    interval = max(0, settings.email_poll_minutes)
    if _scheduler_started or interval <= 0:
        return False
    if not (settings.gmail_user and _app_password()):
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            has_operator_mailbox = bool(_operator_mailboxes(db))
        finally:
            db.close()
        if not has_operator_mailbox:
            logger.info("email poller disabled: no mailbox configured (shared or per-operator)")
            return False

    def _loop():
        from app.db.session import SessionLocal

        # Small initial delay so app startup finishes first.
        time.sleep(15)
        while True:
            cycle_start = datetime.now(timezone.utc)
            db = SessionLocal()
            try:
                res = pull_inbox(db, tenant_id=None, skip_if_busy=True)
                created = sum(1 for m in (res.get("processed") or []) if m.get("job_id"))
                if created:
                    logger.info("email poller: created %s job(s) this cycle", created)
                if res.get("busy"):
                    logger.info("email poller cycle skipped: the previous cycle was still "
                               "running - no mailbox was checked this tick")
                else:
                    logger.info(
                        "email poller cycle done: %s/%s mailbox(es) checked (%s failed), "
                        "%s job(s) created",
                        res.get("mailboxes_ok", 0), res.get("mailboxes_total", 0),
                        len(res.get("mailbox_errors") or []), created,
                    )
                _record_cycle_status(res, cycle_start, db)
            except Exception:  # noqa: BLE001
                logger.exception("email poller cycle failed")
            finally:
                db.close()
            time.sleep(interval * 60)

    threading.Thread(target=_loop, daemon=True, name="email-poller").start()
    _scheduler_started = True
    logger.info("email poller started: every %s min for %s", interval, settings.gmail_user)
    return True


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    out = []
    for part, enc in decode_header(raw):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(part.decode("utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out).strip()


def _app_password() -> str:
    # Gmail shows the app password in 4 spaced groups; it authenticates with or without
    # the spaces — strip them so a copy-paste with spaces still works.
    return (get_settings().gmail_app_password or "").replace(" ", "")


def _imap_host_for(provider: str | None) -> str:
    return "imap.zoho.com" if provider == "zoho" else "imap.gmail.com"


def _connect(email: str | None = None, app_password: str | None = None,
              host: str | None = None) -> imaplib.IMAP4_SSL:
    """With no arguments, connects to the tenant's shared inbox (GMAIL_USER / GMAIL_APP_
    PASSWORD). Passed an operator's own credentials instead, connects to their mailbox."""
    settings = get_settings()
    use_email = email or settings.gmail_user
    use_password = (app_password if app_password is not None else _app_password()) or ""
    use_password = use_password.replace(" ", "")
    use_host = host or settings.imap_host
    if not use_email or not use_password:
        raise RuntimeError("Mailbox credentials are not configured.")
    conn = imaplib.IMAP4_SSL(use_host)
    conn.login(use_email, use_password)
    return conn


def _operator_mailboxes(db: Session, tenant_id: str | None = None) -> list[dict]:
    """Every active, non-paused operator with their own mailbox connected, credentials
    decrypted and ready to hand to _connect(). A mailbox that fails to decrypt (a rotated JWT
    secret, most likely) is skipped rather than raised — the shared inbox and every other
    operator's mailbox must still get polled.

    User.mail_paused (a per-mailbox switch, Super Admin's Settings page) is excluded here the
    same way the system-wide email_pull_paused flag is checked before this function is ever
    called — one mailbox can be stopped without touching anyone else's."""
    from app.models.user import OPERATOR, User

    query = db.query(User).filter(
        User.role == OPERATOR, User.is_active.is_(True),
        User.mail_email.isnot(None), User.mail_app_password_encrypted.isnot(None),
        User.mail_paused.is_(False),
    )
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    out = []
    for u in query.all():
        try:
            from app.core.mail_crypto import decrypt_secret

            password = decrypt_secret(u.mail_app_password_encrypted)
        except Exception:  # noqa: BLE001
            logger.exception("could not decrypt mail credentials for operator %s", u.id)
            continue
        out.append({
            "operator_id": u.id, "email": u.mail_email, "password": password,
            # The exact host verified at connect time (matters for Zoho's regional data
            # centers) - a mailbox connected before this existed has none, so it falls
            # back to the provider's global default until re-verified.
            "host": u.mail_host or _imap_host_for(u.mail_provider),
        })
    return out


def test_connection() -> dict:
    """Verify the mailbox is reachable and report how many messages / how many unread."""
    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    try:
        conn.select("INBOX")
        total = conn.search(None, "ALL")[1]
        unseen = conn.search(None, "UNSEEN")[1]
        n_total = len(total[0].split()) if total and total[0] else 0
        n_unseen = len(unseen[0].split()) if unseen and unseen[0] else 0
        return {
            "ok": True,
            "user": get_settings().gmail_user,
            "mailbox": "INBOX",
            "total": n_total,
            "unseen": n_unseen,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _extract_attachments(msg: email.message.Message) -> list[tuple[str, bytes]]:
    """Real attachments only - never an embedded signature image. The two conditions this
    used to have were redundant with each other (the second unconditionally re-applied the
    first's own extension check), so Content-Disposition was never actually consulted: any
    part with a matching extension counted, including one marked "inline" - exactly how
    Outlook/Gmail/Apple Mail embed a signature logo (filename="logo.png"). That meant a
    routine signature image paid for a full OCR + AI classification pass on every matched
    email, and could single-handedly stop an attachment-less email from taking the cheap
    no_documents short-circuit."""
    files: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if "inline" in disp:
            continue
        fname = _decode(part.get_filename())
        if not fname:
            continue
        if Path(fname).suffix.lower() not in ATTACH_EXTS:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            files.append((Path(fname).name, payload))
    return files


def _addresses(msg: email.message.Message) -> set[str]:
    people = getaddresses(
        msg.get_all("From", []) + msg.get_all("To", []) + msg.get_all("Cc", [])
    )
    return {addr.lower() for _name, addr in people if addr}



def _slot_reference(document_id: str, max_chars: int = 1400) -> str:
    """What this slot's document actually LOOKS LIKE, from the sample uploaded when the
    template was made.

    This is the best reference there is, and it was sitting unused. A slot's field labels and a
    generic hint describe a document type in the abstract; the sample is THIS customer's real
    paperwork - their forwarder's letterhead, their wording, their layout. A freight certificate
    that calls itself an ARRIVAL NOTICE is obvious against the sample and invisible against the
    words "freight certificate".

    Reads the OCR cache written when the template was built. `get_page_ocr` caches to disk, so
    the first pull for a customer may pay for a page or two and every pull after it is free. A
    sample that cannot be read returns "" and the slot falls back to its hint and fields.
    """
    from app.api.v1.template_documents import document_dir
    from app.core.docai import get_page_ocr

    try:
        ddir = document_dir(document_id)
        if not (ddir / "pages" / "page_1.png").exists():
            return ""
        parts = []
        for page in (1, 2):
            if not (ddir / "pages" / f"page_{page}.png").exists():
                break
            text = (get_page_ocr(ddir, page) or {}).get("text") or ""
            if text.strip():
                parts.append(text.strip())
            if sum(len(x) for x in parts) >= max_chars:
                break
        import re

        return re.sub(r"\s+", " ", " ".join(parts))[:max_chars]
    except Exception:  # noqa: BLE001
        logger.warning("no sample text available for template document %s", document_id)
        return ""


def _message_id(msg: email.message.Message) -> str:
    """A stable identity for this message, so it is examined exactly once.

    Message-ID is assigned by the sending server and survives re-fetching, re-flagging and
    being moved between folders. A message without one is rare and usually machine-generated;
    a digest of the headers that do exist stands in, which is stable for the same message.
    """
    import hashlib

    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid[:998]
    seed = "|".join([
        _decode(msg.get("From")), _decode(msg.get("Subject")), (msg.get("Date") or ""),
    ])
    return "synthetic:" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()


def _body_text(msg: email.message.Message) -> str:
    """The message's own words. A hint about the shipment, never the deciding evidence."""
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and "attachment" not in (
                part.get("Content-Disposition") or "").lower():
            try:
                raw = part.get_payload(decode=True) or b""
                return raw.decode(part.get_content_charset() or "utf-8", "replace").strip()
            except Exception:  # noqa: BLE001
                continue
    return ""


def _candidate_groups(db: Session, tenant_id: str | None,
                      operator_id: str | None) -> list[TemplateGroup]:
    """Every customer this pull is allowed to route to.

    Note what is NOT here: a filter on pull_email. Which customer a document belongs to is read
    off the document, so a customer with no address configured is still a candidate - that is
    exactly the case (a first-time sender) the old address matching could never handle.

    `operator_id` scopes to ONE operator's OWN connected mailbox (see _operator_mailboxes) -
    the shared inbox is never scoped this way (it always passes None; a shared customer's mail
    is matched against every template, and which operator SEES the resulting job is decided
    afterwards, from that template's own pull_operator_id, once the match is known).

    A personal mailbox is scoped by this operator's own TEMPLATE ASSIGNMENTS (the same "which
    templates can this operator use" list the wizard and available_groups already use) - NOT
    by pull_operator_id, which is a single field and so could only ever name ONE operator per
    template. Several operators, each with their own connected mailbox, need to be able to
    share the very same template - Sea Import handled by both Priya and Arun, say - and that
    is only possible if each one's own mailbox is scoped by their OWN assignment, not by a
    field that can hold just one operator's id at a time.
    """
    query = db.query(TemplateGroup)
    if tenant_id:
        query = query.filter(TemplateGroup.tenant_id == tenant_id)
    if operator_id:
        from app.models.user_template import UserTemplateAssignment

        assigned = [
            a.group_id for a in
            db.query(UserTemplateAssignment).filter(UserTemplateAssignment.user_id == operator_id).all()
        ]
        # No assignments recorded = this operator may use every one of their tenant's
        # templates (same convention as available_groups), not zero.
        if assigned:
            query = query.filter(TemplateGroup.id.in_(assigned))
    return query.all()


def _clean_example(raw: str) -> str:
    """Tidy an OCR example enough to be worth matching on.

    A mark's example is whatever the scanner read off the customer's own sample document, so it
    arrives with the label echoed back and the value repeated: "Consignee CAMPINA CAMPINA Phone
    1 CAMERON B P HASDUE : 0". Noisy, but it is still that customer's real paperwork and it
    names them - which is the whole point of matching on document data.
    """
    import re

    text = re.sub(r"\s+", " ", (raw or "")).strip(" :;,.-	")
    if len(text) < 4 or not re.search(r"[A-Za-z]{3}", text):
        return ""            # ": 0" and friends: nothing to match on
    return text[:160]


def _identifiers_for(db: Session, group: TemplateGroup) -> dict:
    """Everything known in advance that could name this customer on their own paperwork.

    Three sources, strongest first:

      1. a STANDING value an admin typed - a consignee name, IE code, GST number. Exact, and an
         IE code match is conclusive.
      2. the EXAMPLE VALUE of an identifying mark. The template was built from this customer's
         own documents, so that example is their real consignee/branch as the scanner read it.
         No one has to configure anything for this to work, which matters: most templates have
         no standing consignee value at all.
      3. the template's own NAME, which is usually the company ("ultratech cement(arakkonam").
         Weakest, and offered only alongside 1 or 2 - a template called "seq" would otherwise
         invite a match on nothing.
    """
    from app.core.classifier import IDENTIFYING_FIELDS
    from app.models.custom_field import CustomField
    from app.models.field_mark import FieldMark
    from app.models.template_document import TemplateDocument

    out: dict[str, str] = {}
    for cf in db.query(CustomField).filter(CustomField.group_id == group.id).all():
        val = (cf.hardcoded_value or "").strip()
        if not val:
            continue
        if any(k in cf.label_name.lower() for k in IDENTIFYING_FIELDS):
            out[cf.label_name] = val[:120]

    doc_ids = [d.id for d in db.query(TemplateDocument)
               .filter(TemplateDocument.group_id == group.id).all()]
    if doc_ids:
        for m in db.query(FieldMark).filter(FieldMark.document_id.in_(doc_ids)).all():
            if not any(k in m.label_name.lower() for k in IDENTIFYING_FIELDS):
                continue
            ex = _clean_example(m.example_value or "")
            if ex and m.label_name not in out:
                out[f"{m.label_name} (read from their own sample document)"] = ex

    if out and (group.name or "").strip():
        out["the customer is called"] = group.name.strip()[:120]
    return out


# How a document names itself. Deliberately regex and not another model call: the reference is
# on the page verbatim, this runs once per attachment, and a wrong file NAME is a cosmetic
# problem where a wrong model bill is not.
_REF_PATTERNS = {
    "BL": [r"\bB\s*/?\s*L\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,24})",
           r"\bBill\s+of\s+Lading\s*(?:No\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,24})",
           r"\b(?:MAWB|HAWB|AWB)\s*(?:No\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,24})"],
    "Invoice": [r"\bInvoice\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,24})"],
    "PackingList": [r"\bInvoice\s*(?:No\.?|Number)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,24})",
                    r"\bB\s*/?\s*L\s*(?:No\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,24})"],
}


def _document_name(doc_type: str, slot_name: str, text: str, ext: str) -> str:
    """Name a document after what it says it is, e.g. BL_1075599313.pdf.

    An attachment called `scan0001.pdf` or `image003.jpg` tells an operator nothing, and every
    job ends up with four files of the same name. The reference on the page does tell them
    something, so it goes in the name; without one, the slot's own name is still better than
    whatever the sender happened to call it.
    """
    import re

    base = re.sub(r"[^A-Za-z0-9]+", "_", (doc_type or slot_name or "document")).strip("_")
    for pattern in _REF_PATTERNS.get(doc_type or "", []):
        m = re.search(pattern, text or "", re.IGNORECASE)
        if m:
            ref = re.sub(r"[^A-Za-z0-9\-]+", "", m.group(1))[:28]
            if len(ref) >= 4:
                return f"{base}_{ref}{ext}"
    safe_slot = re.sub(r"[^A-Za-z0-9]+", "_", slot_name or "").strip("_")
    if safe_slot and safe_slot.lower() != base.lower():
        return f"{base}_{safe_slot}{ext}"
    return f"{base}{ext}"


# A message claimed but not finished within this long is assumed to belong to a run that died,
# and may be taken over. Long enough that a slow job (OCR + two model calls per document) is
# never stolen mid-flight; short enough that a crash does not strand a customer's mail for a
# working day.
CLAIM_STALE_MINUTES = 30

_pull_lock = threading.Lock()


def _claim(db: Session, message_id: str, sender: str, subject: str,
           steal: bool = False) -> tuple[bool, str]:
    """Take ownership of a message BEFORE doing the work. (claimed, why-not).

    The whole point is the gap. Reading a message takes minutes - OCR every page, then two
    model calls per document - and the mail stayed unread and unlogged for all of it. Anything
    that looked at the mailbox in that window saw a brand new message and built the job a
    second time, which is exactly what happened: two jobs, same customer, same four slots, and
    two separate copies of every document.

    `message_id` is UNIQUE, so the insert itself is the lock, and it holds across processes and
    restarts - not just between two threads of one server.
    """
    row = EmailSeen(message_id=message_id, sender=sender[:320] or None,
                    subject=(subject or "")[:500] or None, verdict="working",
                    note="being read now")
    try:
        db.add(row)
        db.commit()
        return True, ""
    except IntegrityError:
        db.rollback()
    existing = db.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
    if existing is None:      # deleted between the insert and the read - let the next poll have it
        return False, "claimed by another run"
    if existing.verdict != "working" and not steal:
        return False, f"already examined ({existing.verdict})"
    if existing.verdict == "working":
        # `steal` only ever means "let reexamine reopen a FINISHED verdict" (the check
        # above) - it must never also mean "take a claim that is actively being read right
        # now, this instant". That used to be gated on `and not steal` too, so a reexamine
        # call racing the background poller's own in-flight claim (0 minutes old, nowhere
        # near CLAIM_STALE_MINUTES) stole it immediately - two runs reading the same
        # message at once, which is the exact two-jobs-one-email bug this whole claim
        # mechanism exists to prevent (see this function's own docstring). Staleness is
        # unconditional for a "working" claim, steal or not.
        started = existing.updated_at or existing.created_at
        # The column stores UTC without a timezone on both SQLite and Postgres, so it comes
        # back naive; subtracting an aware "now" from it raises. Treat naive as the UTC it is.
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - started).total_seconds() / 60 if started else 0
        if age < CLAIM_STALE_MINUTES:
            return False, "another run is reading it now"
        logger.warning("taking over message %s, claimed %.0f minutes ago and never finished",
                       message_id[:60], age)
    # Ours now. Stamp it EXPLICITLY: when a stale claim is taken over, verdict and note already
    # hold these very values, so SQLAlchemy sees no change, issues no UPDATE, and `onupdate`
    # never fires - leaving the row as old as it was. The next run would then find it stale too
    # and take it over as well, which is the duplicate all over again.
    existing.verdict = "working"
    existing.note = "being read now"
    existing.updated_at = datetime.now(timezone.utc)
    db.commit()
    return True, ""


def _record(db: Session, message_id: str, sender: str, subject: str, verdict: str,
            note: str = "", group_id: str | None = None, job_id: str | None = None,
            evidence: str = "") -> None:
    """Write down that this message has been examined, and what we concluded.

    Committed on its own: if the caller later fails, the record of having looked must still
    stand, or the next poll pays for the same OCR and the same model calls all over again.
    """
    try:
        row = db.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
        if row is None:
            row = EmailSeen(message_id=message_id)
            db.add(row)
        row.sender = (sender or "")[:320]
        row.subject = (subject or "")[:500]
        row.verdict = verdict
        row.note = note or None
        row.group_id = group_id
        row.job_id = job_id
        row.evidence = evidence or None
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not record email verdict for %s", message_id)
        db.rollback()


def _unclaim(db: Session, message_id: str) -> None:
    """Undo _claim(): delete the EmailSeen row entirely, so a LATER poll - an ordinary one,
    not only reexamine - tries this message again completely fresh.

    Used when processing stopped for a reason that has nothing to do with the message itself
    (the AI service was unavailable - see AIServiceUnavailable). Writing ANY verdict here,
    even a neutral one, would look like a considered decision was made about this message,
    when the truth is closer to "we never actually got to look".
    """
    try:
        db.query(EmailSeen).filter(EmailSeen.message_id == message_id).delete()
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not clear the claim for %s after a deferred pull", message_id[:60])
        db.rollback()


def _close_any_pending_email_for(db: Session, message_id: str, group_id: str, job_id: str) -> None:
    """A message that just succeeded (matched a customer, built a job) may already have a
    "pending" row from an EARLIER pull that couldn't place it - realistically, an
    identifier missing at the time, later added, and the same message reexamined. Nothing
    ever cross-referenced PendingEmail against a later success: the row sat there reading
    "pending" forever, so a Super Admin working the Pending Emails queue could resolve it
    by hand and build a SECOND job for the exact same original email - confirmed live.
    Marks it resolved against the job that was actually just created, and removes its now-
    redundant attachment files the same way a normal resolve/dismiss does."""
    row = (
        db.query(PendingEmail)
        .filter(PendingEmail.message_id == message_id, PendingEmail.status == "pending")
        .one_or_none()
    )
    if row is None:
        return
    import shutil

    row.status = "resolved"
    row.resolved_group_id = group_id
    row.resolved_job_id = job_id
    shutil.rmtree(Path(get_settings().uploads_dir) / "pending_email" / row.id, ignore_errors=True)


def _save_pending_email(db: Session, message_id: str, sender: str, subject: str,
                        reason: str, evidence: str, prepared: list[dict]) -> None:
    """Hold a message's attachments for a person to assign a template to by hand, for the one
    case _record() alone used to be the whole story of: the documents did not name a customer
    we know, or named more than one. Throwing the files away there was right when nobody could
    ever act on them - now someone can, so they are kept instead, under their own row rather
    than EmailSeen's (which stays exactly as it was, so the poller still never re-reads this
    message on its own).
    """
    import uuid

    try:
        row = PendingEmail(
            message_id=message_id, sender=(sender or "")[:320] or None,
            subject=(subject or "")[:500] or None, reason=reason or None,
            evidence=evidence or None, status="pending",
        )
        db.add(row)
        db.flush()
        pdir = Path(get_settings().uploads_dir) / "pending_email" / row.id
        pdir.mkdir(parents=True, exist_ok=True)
        attachments = []
        for item in prepared:
            fname = f"{uuid.uuid4().hex[:8]}_{(item.get('name') or 'file')[:200]}"
            (pdir / fname).write_bytes(item["blob"])
            attachments.append({
                "name": item.get("name") or fname, "ext": item.get("ext") or "",
                "path": fname, "text": (item.get("text") or "")[:20000],
                # Needed to split a combined file (Invoice + Packing List in one PDF) into
                # each slot's own pages when this is resolved later - see extract_pdf_pages.
                "drop_pages": item.get("drop_pages") or [], "page_count": item.get("page_count") or 0,
            })
        row.attachments = attachments
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not save pending email for %s", message_id)
        db.rollback()


def pull_inbox(
    db: Session, tenant_id: str | None = None, max_messages: int = 30,
    operator_id: str | None = None, reexamine: bool = False, skip_if_busy: bool = False,
) -> dict:
    """Read unread mail, decide from the DOCUMENTS whose it is, and create the job.

    `tenant_id` limits to one tenant's customers; `operator_id` further limits to customers
    routed to that operator. `reexamine=True` ignores the "already examined" record, for when a
    customer has just been configured and their earlier mail should be looked at again.

    `skip_if_busy` is for the BACKGROUND poller only, so its timer cycles cannot stack up on a
    slow mailbox. A person pressing Check mail is never turned away: correctness comes from
    _claim(), which hands each message to exactly one run, so a second run alongside the poller
    is safe - it simply skips whatever is already being read and reports on the rest. Blocking
    it instead told an operator "try again later" and did nothing, which is a worse answer than
    "no new mail" and was never needed to stop the duplicate.
    """
    from app.core.system_settings import is_email_pull_paused

    if is_email_pull_paused(db):
        # A Super Admin turned this off - before connecting to a single mailbox, not after
        # one fails. Nothing to unclaim here: that already happened the moment the pause was
        # switched on (see app/api/v1/system_settings.py), so every message is already sitting
        # exactly as available as it was before this poll would have touched it.
        return {"ok": True, "processed": [], "note": "Email pull is currently paused by an administrator."}
    if skip_if_busy:
        if not _pull_lock.acquire(blocking=False):
            logger.info("scheduled email pull skipped: a run is already in progress")
            return {"ok": True, "processed": [], "busy": True,
                    "note": "A run was already in progress; this cycle was skipped."}
        try:
            return _pull_inbox(db, tenant_id, max_messages, operator_id, reexamine)
        finally:
            _pull_lock.release()
    return _pull_inbox(db, tenant_id, max_messages, operator_id, reexamine)


def _pull_inbox(
    db: Session, tenant_id: str | None = None, max_messages: int = 30,
    operator_id: str | None = None, reexamine: bool = False,
) -> dict:
    """Poll every mailbox this call is allowed to read, and merge the results: the tenant's
    shared inbox (only when not restricted to one operator — a personal mailbox and the
    shared one are different sources of mail, not alternatives), plus every operator's own
    connected mailbox. Held under _pull_lock by its caller."""
    settings = get_settings()
    sources: list[dict] = []
    if operator_id is None and settings.gmail_user and _app_password():
        sources.append({"operator_id": None, "email": None, "password": None, "host": None})
    for m in _operator_mailboxes(db, tenant_id):
        if operator_id and m["operator_id"] != operator_id:
            continue
        sources.append(m)

    if not sources:
        note = ("No mailbox is connected for you yet — add one under Operator Creation."
                if operator_id else "No mailbox is configured yet.")
        return {"ok": True, "processed": [], "note": note}

    # Read every mailbox AT ONCE rather than one after another. Reading a message is minutes
    # of work — OCR every page, then AI calls per document — so a sequential loop meant one
    # busy mailbox held up every OTHER connected mailbox behind it: the tenth one on the list
    # could sit unchecked through an entire poll cycle, then the next, then the one after.
    # Each thread gets its OWN session — a SQLAlchemy session is not safe to share across
    # threads, and one mailbox's slow OCR/AI calls must never block another's DB writes.
    def _pull_with_own_session(src: dict) -> dict:
        from app.db.session import SessionLocal

        worker_db = SessionLocal()
        try:
            return _pull_one_mailbox(
                worker_db, tenant_id, max_messages, src["operator_id"], reexamine,
                mail_email=src["email"], mail_app_password=src["password"], mail_host=src["host"],
            )
        finally:
            worker_db.close()

    all_processed: list[dict] = []
    mailbox_errors: list[str] = []
    combined_note: str | None = None
    from app.core.system_settings import get_email_poll_workers

    workers = max(1, min(get_email_poll_workers(db), len(sources)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="email-pull") as pool:
        # ThreadPoolExecutor IS the worker queue: `workers` threads run concurrently, and the
        # moment one finishes its mailbox, it picks the next one waiting off the pool's own
        # internal queue - nothing more to build for that part.
        futures = {pool.submit(_pull_with_own_session, src): src for src in sources}
        for future in as_completed(futures):
            src = futures[future]
            label = src["email"] or "shared inbox"
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("mailbox pull crashed for %s", label)
                mailbox_errors.append(f"{label}: {exc}")
                continue
            if not result.get("ok"):
                mailbox_errors.append(f"{label}: {result.get('error')}")
                continue
            all_processed.extend(result.get("processed") or [])
            if result.get("note"):
                combined_note = result["note"]

    out: dict = {
        "ok": True, "processed": all_processed,
        "mailboxes_total": len(sources),
        "mailboxes_ok": len(sources) - len(mailbox_errors),
    }
    if combined_note and not mailbox_errors:
        out["note"] = combined_note
    if mailbox_errors:
        out["mailbox_errors"] = mailbox_errors
    return out


def _pull_one_mailbox(
    db: Session, tenant_id: str | None = None, max_messages: int = 30,
    operator_id: str | None = None, reexamine: bool = False,
    mail_email: str | None = None, mail_app_password: str | None = None,
    mail_host: str | None = None,
) -> dict:
    """Everything pull_inbox used to do end to end, now for exactly ONE mailbox."""
    from app.api.v1.jobs import _job_doc_dir, _render_pages, generate_job_no, run_extraction
    from app.core.classifier import AIServiceUnavailable, assign_documents_detailed, identify_customer
    from app.core.docai import ocr_page_image
    from app.core.page_filter import extract_pdf_pages, kept_page_to_original
    from app.core.custom_page_filter import filter_pages_with_custom, get_active_custom_filter_texts

    custom_filter_texts = get_active_custom_filter_texts(db)
    groups = _candidate_groups(db, tenant_id, operator_id)
    if not groups:
        note = ("No customers are routed to you yet - ask your admin to assign one to you."
                if operator_id else "No customers are configured yet.")
        return {"ok": True, "processed": [], "note": note}

    # A personal mailbox connected to exactly ONE template has nowhere else its mail could
    # possibly belong - unlike a shared inbox serving several different customers at once
    # (a forwarding agent mailing for six importers from one address), there is no ambiguity
    # here for the documents' content to resolve. Every message is trusted to be for this one
    # template directly, whether or not the exporter/consignee on it has ever been configured
    # as a known identifier - that requirement only exists to arbitrate BETWEEN customers, and
    # there is only one here. An operator connected to several templates (or the shared inbox,
    # operator_id=None) still needs the documents to say who they are.
    single_group = groups[0] if (operator_id and len(groups) == 1) else None

    ident: dict[str, dict] = {}
    candidates: list[dict] = []
    unidentifiable: list[str] = []
    if single_group is None:
        # Only customers whose paperwork carries something identifying can be recognised
        # from content. Say so plainly rather than silently never matching them.
        ident = {g.id: _identifiers_for(db, g) for g in groups}
        unidentifiable = [g.name for g in groups if not ident.get(g.id)]
        candidates = [
            {"key": g.id, "name": g.name, "entry_mode": (g.entry_mode or "fields"),
             "identifiers": ident[g.id]}
            for g in groups if ident.get(g.id)
        ]
        if not candidates:
            return {
                "ok": True, "processed": [],
                "note": ("No customer has anything on their documents that identifies them "
                         "(a consignee name, IE code or GST number configured as a standing "
                         "value). Add one to a template and pull again."),
                "unidentifiable": unidentifiable,
            }

    by_id = {g.id: g for g in groups}

    try:
        conn = _connect(mail_email, mail_app_password, mail_host)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    processed: list[dict] = []
    try:
        conn.select("INBOX")
        # A routine poll only looks at today's mail - midnight to midnight. SINCE/BEFORE are
        # date-only (no time of day) and compare against the message's arrival date as the
        # mail SERVER recorded it, so this is exactly "today" regardless of what time the
        # poll happens to run.
        #
        # UNSEEN normally, so a routine poll never re-reads mail a person has already opened
        # for their own reasons - but reexamine explicitly means "look again at something
        # already handled", and a message that succeeded is deliberately left read (or was
        # read by a human) - the exact case UNSEEN would silently skip regardless of
        # reexamine, since that flag only bypasses OUR OWN "already examined" record
        # (_claim), not this search. Widening to ALL here is what makes reexamine able to
        # reach it at all.
        #
        # The date bound must widen for reexamine too, not just the seen/unseen criterion -
        # its whole documented purpose is reaching a message that arrived BEFORE today and
        # was deliberately left unmatched (a customer's first email, sitting in Pending Mail
        # until someone finishes configuring their template), and that is realistically
        # discovered and re-triggered a day or more later, not within the same SINCE/BEFORE
        # window still bounding it to "today" used to leave in place. max_messages below
        # still caps how much a wide-open search can return.
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        date_fmt = "%d-%b-%Y"  # IMAP's required form, e.g. "01-Jan-2026"
        seen_criterion = "ALL" if reexamine else "UNSEEN"
        if reexamine:
            typ, data = conn.search(None, seen_criterion)
        else:
            typ, data = conn.search(
                None, seen_criterion, "SINCE", today.strftime(date_fmt), "BEFORE", tomorrow.strftime(date_fmt)
            )
        uids = data[0].split() if data and data[0] else []
        uids = uids[-max_messages:]

        for uid in uids:
            # PEEK, so looking does not mark it read. Only a message that becomes a job does.
            typ, raw = conn.fetch(uid, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            sender = parseaddr(msg.get("From"))[1].lower()
            subject = _decode(msg.get("Subject")) or "(no subject)"
            mid = _message_id(msg)

            # Claim it BEFORE reading it, not after. See _claim().
            claimed, why_not = _claim(db, mid, sender, subject, steal=reexamine)
            if not claimed:
                processed.append({
                    "from": sender, "subject": subject, "matched": None,
                    "reason": why_not, "skipped_by_log": True,
                })
                continue

            # ---- attachments, including whatever is inside a .zip
            import io
            import zipfile

            files: list[tuple[str, bytes]] = []
            for name, blob in _extract_attachments(msg):
                if name.lower().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(blob)) as z:
                            for n in z.namelist():
                                if not n.endswith("/") and \
                                        Path(n).suffix.lower() in CLASSIFY_EXTS:
                                    files.append((Path(n).name, z.read(n)))
                    except zipfile.BadZipFile:
                        continue
                elif Path(name).suffix.lower() in CLASSIFY_EXTS:
                    files.append((name, blob))

            if not files:
                _record(db, mid, sender, subject, "no_documents",
                        "nothing readable was attached")
                processed.append({"from": sender, "subject": subject, "matched": None,
                                  "reason": "no document attachments"})
                continue

            # ---- read every page, and hold the text only as long as it is needed
            import shutil

            safe_mid = "".join(ch for ch in mid if ch.isalnum() or ch in "-_")[:60] or "msg"
            tmp = Path(get_settings().uploads_dir) / "_email" / safe_mid
            doc_texts: list[tuple[str, str]] = []
            prepared: list[dict] = []
            group = None
            filled: list[str] = []
            named: list[str] = []
            who: dict = {}
            tie_note = ""
            job = None
            try:
                import fitz  # PyMuPDF

                for idx, (name, blob) in enumerate(files):
                    ext = Path(name).suffix.lower() or ".pdf"
                    fdir = tmp / str(idx)
                    (fdir / "pages").mkdir(parents=True, exist_ok=True)
                    orig = fdir / f"f{ext}"
                    orig.write_bytes(blob)
                    text, first_png, pages = "", None, 0
                    try:
                        with fitz.open(orig) as doc:
                            for i, page in enumerate(doc, start=1):
                                if i > 6:
                                    break
                                png = fdir / "pages" / f"page_{i}.png"
                                page.get_pixmap(dpi=150).save(png)
                                if i == 1:
                                    first_png = png
                                pages = i
                        raw_pages = []
                        for i in range(1, pages + 1):
                            try:
                                got = ocr_page_image(
                                    fdir / "pages" / f"page_{i}.png").get("text", "")
                            except Exception:  # noqa: BLE001
                                logger.warning("OCR unavailable for %s p%s", name, i)
                                got = ""
                            raw_pages.append(got)
                        # Drop the carrier's terms and conditions BEFORE classifying, not just
                        # before extracting. A 2-page sea waybill is 42,551 characters of which
                        # ~39,000 are terms that define "Freight", "Package", "Packing List"
                        # and "invoice" - so the bill of lading matched every slot on the
                        # template and filled all four. The data page on its own is
                        # unambiguous.
                        kept, dropped = filter_pages_with_custom(raw_pages, custom_filter_texts, name)
                        if dropped:
                            logger.info("email classify: %s - ignoring page(s) %s, carrier "
                                        "terms and conditions", name, dropped)
                        text = "\n\n".join(
                            f"=== PAGE {n} ===\n{t}"
                            for n, t in enumerate(kept, start=1) if t.strip())
                    except Exception:  # noqa: BLE001
                        logger.warning("could not open attachment %s", name)
                        continue
                    prepared.append({"name": name, "ext": ext, "blob": blob,
                                     "text": text, "image": first_png,
                                     "drop_pages": list(dropped), "page_count": pages})
                    doc_texts.append((name, text))

                if not prepared:
                    _record(db, mid, sender, subject, "unreadable",
                            "the attachments could not be opened")
                    processed.append({"from": sender, "subject": subject, "matched": None,
                                      "reason": "attachments could not be read"})
                    continue

                # ---- WHOSE documents are these? Decided from the pages, not the sender -
                # unless this mailbox only ever answers that question one way.
                if single_group is not None:
                    who = {"keys": [single_group.id],
                           "reason": "the only template connected to this mailbox",
                           "evidence": ""}
                else:
                    who = identify_customer(subject, _body_text(msg), doc_texts, candidates)
                keys = who.get("keys") or []

                if not keys:
                    # Nothing recognised. Throw the extracted text and the files away - keeping
                    # a stranger's documents on disk serves nobody - and write down why, so the
                    # next poll does not pay for the same OCR and the same model call again.
                    reason = who.get("reason") or "the documents name no customer we know"
                    _record(db, mid, sender, subject, "no_customer", reason,
                            evidence=who.get("evidence") or "")
                    _save_pending_email(db, mid, sender, subject, reason,
                                       who.get("evidence") or "", prepared)
                    processed.append({
                        "from": sender, "subject": subject, "matched": None,
                        "reason": "the documents do not identify any customer",
                        "why": who.get("reason"), "evidence": who.get("evidence"),
                        "left_unread": True,
                    })
                    continue

                chosen_id = keys[0]
                if len(keys) > 1:
                    # Two customers whose paperwork looks the same. Prefer the Excel-entry one:
                    # a deliberate rule rather than a coin toss, and it is written on the row so
                    # a wrong call can be found afterwards.
                    excel = [k for k in keys if (by_id[k].entry_mode or "fields") == "excel"]
                    if len(excel) == 1:
                        chosen_id = excel[0]
                        tie_note = (f"{len(keys)} customers fit; chose the Excel-entry one "
                                    f"({by_id[chosen_id].name})")
                    else:
                        names = ", ".join(by_id[k].name for k in keys)
                        amb_reason = (f"fits more than one customer and none is the only "
                                     f"Excel-entry one: {names}")
                        _record(db, mid, sender, subject, "ambiguous", amb_reason,
                                evidence=who.get("evidence") or "")
                        _save_pending_email(db, mid, sender, subject, amb_reason,
                                           who.get("evidence") or "", prepared)
                        processed.append({
                            "from": sender, "subject": subject, "matched": None,
                            "reason": f"fits more than one customer ({names}) - needs a person",
                            "candidates": [by_id[k].name for k in keys],
                            "left_unread": True,
                        })
                        continue

                group = by_id[chosen_id]

                # Safety net: this can only happen if a tenant's allowed modes were narrowed
                # AFTER this template was built (template creation itself already refuses to
                # tag a template with a mode the tenant isn't licensed for) - but a job filed
                # under a mode nobody signed off on is worse than one held for a person to
                # look at, so treat it exactly like not recognising the customer at all.
                tenant = db.get(Tenant, group.tenant_id)
                if group.mode and tenant is not None and tenant.allowed_modes \
                        and group.mode not in tenant.allowed_modes:
                    mode_reason = (f"identified {group.name}, but its mode ({group.mode}) "
                                  f"is not one {tenant.name} is licensed for")
                    _record(db, mid, sender, subject, "no_customer", mode_reason,
                            evidence=who.get("evidence") or "")
                    _save_pending_email(db, mid, sender, subject, mode_reason,
                                       who.get("evidence") or "", prepared)
                    processed.append({
                        "from": sender, "subject": subject, "matched": None,
                        "reason": mode_reason, "left_unread": True,
                    })
                    continue

                # ---- the job
                # Every new job starts Unassigned, even a mail-pulled one - assignment is now
                # always a deliberate action from the Jobs list' own dropdown, never an
                # automatic side effect of whose mailbox it happened to arrive in.
                job = Job(
                    tenant_id=group.tenant_id,
                    group_id=group.id,
                    reference=generate_job_no(),
                    status="draft",
                    created_by_id=None,
                    assigned_operator_id=None,
                )
                db.add(job)
                db.flush()
                slots: dict[str, JobDocument] = {}
                slot_meta: dict[str, dict] = {}
                for tdoc in group.documents:
                    jd = JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                                     template_document_id=tdoc.id, page_count=0)
                    db.add(jd)
                    slots[tdoc.id] = jd
                    slot_meta[tdoc.id] = {
                        "key": tdoc.id, "name": tdoc.name, "doc_type": tdoc.doc_type,
                        "fields": [m.label_name for m in tdoc.marks],
                        # the customer's own sample for this slot - the strongest reference
                        "reference": _slot_reference(tdoc.id),
                    }
                db.flush()
                cand_slots = list(slot_meta.values())

                # assign_documents, not classify_file per file: a slot goes to ONE file, and
                # the most specific claimant wins it. Classifying each file alone let a single
                # invoice fill both the Invoice and the Packing List slot, and a bill of lading
                # fill both the BL and the Freight slot - the real packing list and freight
                # certificate were silently dropped.
                claims_per_file = assign_documents_detailed(prepared, cand_slots)
                for item, claims in zip(prepared, claims_per_file):
                    keys = [c["key"] for c in claims]
                    # Say what the analyser decided, per file. Without this a document that
                    # goes unrouted leaves no trace at all: the job simply has an empty slot
                    # and nothing to explain why. The character count matters too - a file the
                    # OCR could not read gives the analyser nothing to work with, and that is
                    # a different problem from a file it read and rejected.
                    logger.info(
                        "email classify: %s (%s chars of text) -> %s",
                        item["name"], len(item["text"] or ""),
                        ", ".join(slot_meta[k]["name"] for k in keys if k in slot_meta)
                        or "NO SLOT MATCHED")
                    # A combined attachment (one PDF carrying both the Invoice and the Packing
                    # List) must give each slot only ITS pages, not the whole thing.
                    split = len(claims) > 1 and item["ext"] == ".pdf"
                    kept_original = (
                        kept_page_to_original(item["page_count"], item["drop_pages"])
                        if split else []
                    )
                    for claim in claims:
                        key = claim["key"]
                        jd = slots.get(key)
                        if jd is None:
                            continue
                        if jd.file_path:
                            # The slot already holds a file, and this mail carries another one
                            # for it — three invoices for one shipment is ordinary. Add a row
                            # rather than writing over the last, which lost the earlier
                            # attachments without a word. slots[key] always points at the
                            # newest row, so file_index keeps counting up.
                            jd = JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                                             template_document_id=key, page_count=0,
                                             file_index=jd.file_index + 1)
                            db.add(jd)
                            db.flush()
                            slots[key] = jd
                        jd.original_name = (item["name"] or "")[:255] or None
                        meta = slot_meta[key]
                        fname = _document_name(meta["doc_type"], meta["name"],
                                               item["text"], item["ext"])
                        ddir = _job_doc_dir(jd.id)
                        ddir.mkdir(parents=True, exist_ok=True)
                        dorig = ddir / fname
                        if split:
                            orig_pages = sorted({
                                kept_original[p - 1] for p in claim["pages"]
                                if 1 <= p <= len(kept_original)
                            }) or kept_original
                            dorig.write_bytes(extract_pdf_pages(item["blob"], orig_pages))
                            jd.page_count = _render_pages(dorig, ddir / "pages")
                        else:
                            dorig.write_bytes(item["blob"])
                            # The carrier's terms page is not part of the document anyone
                            # works with, so it is not rendered into the job either. The
                            # original file still holds it.
                            jd.page_count = _render_pages(dorig, ddir / "pages",
                                                          skip=item.get("drop_pages"))
                        jd.file_path = str(dorig)
                        if meta["name"] not in filled:
                            filled.append(meta["name"])
                        named.append(f"{item['name']} -> {fname}")
                db.commit()
                # The pipeline's own working copy of this email is deleted in the finally
                # block below the moment this job is done processing - this is the only chance
                # to keep the as-arrived original for GK1's IRN Documents Upload "Prealert"
                # view.
                from app.core.job_email import save_original_email

                save_original_email(job.id, msg, files, sender, subject)
            except AIServiceUnavailable as exc:
                # The AI service itself failed (quota, rate limit, connection, 5xx) - this
                # message was never actually looked at, so it must not be recorded as "no
                # customer" or "no document matched". Undo the claim entirely: the very next
                # poll - an ordinary one, not only reexamine - tries it again from scratch,
                # once the service has recovered. Nothing was ever marked read (attachments
                # are read via BODY.PEEK[]), so there is no flag to undo either.
                db.rollback()
                logger.warning(
                    "email pull for %s deferred - AI service unavailable (%s); this message "
                    "will be retried automatically on a later cycle", mid[:60], exc)
                _unclaim(db, mid)
                processed.append({
                    "from": sender, "subject": subject, "matched": None,
                    "reason": "the AI service is temporarily unavailable - retrying "
                              "automatically on a later poll",
                    "deferred": True,
                })
                continue
            finally:
                # The temporary pages and OCR output go, whatever happened. Anything the job
                # needs has already been copied into the job's own folder.
                shutil.rmtree(tmp, ignore_errors=True)

            if group is None or job is None:
                continue

            if not filled:
                # The customer was recognised, but not one attachment belongs in any of their
                # slots. Keeping the job would put an empty shell in the operator's queue, and
                # marking the mail read would make it unpullable - so the job goes and the mail
                # stays. Usually it means the template is missing a document type, and once
                # that is added the same mail can be pulled again.
                for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
                    db.delete(jd)
                db.delete(job)
                db.commit()
                _record(db, mid, sender, subject, "unrouted",
                        f"identified {group.name}, but none of the {len(files)} attachment(s) "
                        f"fits any of their document slots",
                        group_id=group.id, evidence=who.get("evidence") or "")
                processed.append({
                    "from": sender, "subject": subject, "matched": group.name,
                    "reason": (f"identified {group.name}, but nothing attached fits their "
                               "document slots — no job created"),
                    "left_unread": True,
                })
                continue

            try:
                run_extraction(db, job)
            except Exception:  # noqa: BLE001
                logger.exception("auto-extract failed for email job %s", job.id)
            # Left UNREAD on purpose, even on success - that is how the inbox itself shows
            # "this one was turned into a job" to a person glancing at it. Processed mail is
            # never re-picked-up by mistake anyway, regardless of this flag: _claim() already
            # recorded it in EmailSeen the moment this run started, and the next poll's
            # _claim() sees that record and skips it outright - the mailbox's own read/unread
            # flag plays no part in that decision.
            try:
                conn.store(uid, "-FLAGS", "\\Seen")
            except Exception:  # noqa: BLE001
                pass
            _close_any_pending_email_for(db, mid, group.id, job.id)
            _record(db, mid, sender, subject, "matched",
                    tie_note or (who.get("reason") or ""), group_id=group.id, job_id=job.id,
                    evidence=who.get("evidence") or "")
            processed.append({
                "from": sender,
                "subject": subject,
                "matched": group.name,
                "matched_by": "the documents' own data",
                "why": who.get("reason"),
                "evidence": who.get("evidence"),
                "tie_break": tie_note or None,
                "job_id": job.id,
                "filled_slots": filled,
                "renamed": named,
                "attachments": len(files),
                # Every new job starts Unassigned now (see the Job() construction above) -
                # this used to report the auto-assigned operator, which no longer exists.
                "assigned_operator_id": None,
            })
        out = {"ok": True, "processed": processed}
        if unidentifiable:
            out["note"] = ("These customers have nothing on their documents that identifies "
                           "them, so mail can never be routed to them: "
                           + ", ".join(unidentifiable))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.exception("email pull failed")
        return {"ok": False, "error": str(exc), "processed": processed}
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass

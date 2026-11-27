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
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import time
from email.header import decode_header
from email.utils import getaddresses, parseaddr
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import email_progress
from app.core.billing import record_billable_pages
from app.core.config import get_settings
from sqlalchemy.exc import IntegrityError

from app.models.email_seen import EmailSeen
from app.models.job import Job, JobDocument, UnmatchedUploadPage
from app.models.job_event import JobEvent
from app.models.pending_email import PendingEmail
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import OPERATOR, User

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
    tenant's shared inbox, or at least one connected (and tenant-assigned) mailbox. Idempotent
    — calling it more than once is a no-op. Returns True if it started."""
    global _scheduler_started
    settings = get_settings()
    interval = max(0, settings.email_poll_minutes)
    if _scheduler_started or interval <= 0:
        return False
    if not (settings.gmail_user and _app_password()):
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            has_connected_mailbox = bool(_connected_mailboxes(db))
        finally:
            db.close()
        if not has_connected_mailbox:
            logger.info("email poller disabled: no mailbox configured (shared or connected)")
            return False

    def _loop():
        from app.db.session import SessionLocal

        # Small initial delay so app startup finishes first.
        time.sleep(15)
        while True:
            cycle_start = datetime.now(timezone.utc)
            db = SessionLocal()
            try:
                res = pull_inbox(db, tenant_id=None, skip_if_busy=True, use_queue=True)
                if res.get("queued"):
                    logger.info("email poller: queued %s mail(s) for the mail workers", res["queued"])
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


def _connected_mailboxes(db: Session, tenant_id: str | None = None) -> list[dict]:
    """Every connected, non-paused, TENANT-ASSIGNED mailbox, credentials decrypted and ready
    to hand to _connect(). Unassigned (tenant_id is null) mailboxes are excluded here, not
    just elsewhere - there is nowhere for their mail to be routed to yet. A mailbox that fails
    to decrypt (a rotated JWT secret, most likely) is skipped rather than raised - every other
    mailbox must still get polled.

    paused (the Email Puller screen's own per-mailbox switch) is excluded here the same way
    the system-wide email_pull_paused flag is checked before this function is ever called -
    one mailbox can be stopped without touching anyone else's."""
    from app.core.mail_crypto import decrypt_secret
    from app.models.connected_mailbox import ConnectedMailbox

    from app.models.tenant import Tenant

    # A switched-off customer's mail is never read (Tenants > Disable).
    active_tenants = db.query(Tenant.id).filter(Tenant.is_active.is_(True))
    query = db.query(ConnectedMailbox).filter(
        ConnectedMailbox.tenant_id.isnot(None), ConnectedMailbox.paused.is_(False),
        ConnectedMailbox.tenant_id.in_(active_tenants),
    )
    if tenant_id:
        query = query.filter(ConnectedMailbox.tenant_id == tenant_id)
    out = []
    for mb in query.all():
        try:
            password = decrypt_secret(mb.app_password_encrypted)
        except Exception:  # noqa: BLE001
            logger.exception("could not decrypt mail credentials for mailbox %s", mb.id)
            continue
        out.append({
            "mailbox_id": mb.id, "email": mb.email, "password": password,
            # The exact host verified at connect time (matters for Zoho's regional data
            # centers) - an empty one (carried over from a pre-this-table mailbox that never
            # had it) falls back to the provider's global default.
            "host": mb.host or _imap_host_for(mb.provider),
            "tenant_id": mb.tenant_id, "group_id": mb.group_id,
        })
    return out


def _operator_matching_sender(db: Session, tenant_id: str, sender: str) -> str | None:
    """The active operator this message's SENDER actually is, if any - matched against their
    own LOGIN email. Not a mailbox-routing rule (see the job-creation comment this feeds): a
    shared inbox forwarding six importers' mail is never "one operator's" jobs, but a message
    FROM a real operator's own address unambiguously already has one.

    Used to also match User.mail_email (an operator's own connected mailbox, back when a
    mailbox was owned by one operator user) - mailboxes are their own thing now (see
    ConnectedMailbox), never tied to a specific operator, so that half no longer has anything
    meaningful to compare against for a mailbox connected since.
    """
    sender = (sender or "").strip().lower()
    if not sender:
        return None
    u = (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id, User.role == OPERATOR, User.is_active.is_(True),
            func.lower(User.email) == sender,
        )
        .first()
    )
    return u.id if u else None


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


def _body_cids(msg: email.message.Message) -> set[str]:
    """Every cid: the HTML body actually displays.

    A part referenced this way IS the letter - the sender's logo, the AEO and FTWZ badges in
    their footer, the little anniversary graphic. It is not a document anybody attached.
    """
    cids: set[str] = set()
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        raw = part.get_payload(decode=True) or b""
        try:
            html = raw.decode(part.get_content_charset() or "utf-8", "replace")
        except (LookupError, UnicodeDecodeError):
            html = raw.decode("utf-8", "replace")
        cids |= {m.strip().lower() for m in re.findall(r"cid:([^\"'>\s)]+)", html, re.I)}
    return cids


def _extract_attachments(msg: email.message.Message, exts: set[str] | None = None) -> list[tuple[str, bytes]]:
    """Real attachments only - never an embedded signature image. The two conditions this
    used to have were redundant with each other (the second unconditionally re-applied the
    first's own extension check), so Content-Disposition was never actually consulted: any
    part with a matching extension counted, including one marked "inline" - exactly how
    Outlook/Gmail/Apple Mail embed a signature logo (filename="logo.png"). That meant a
    routine signature image paid for a full OCR + AI classification pass on every matched
    email, and could single-handedly stop an attachment-less email from taking the cheap
    no_documents short-circuit."""
    files: list[tuple[str, bytes]] = []
    body_cids = _body_cids(msg)
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        if "inline" in disp:
            continue
        # Content-Disposition: inline is only ONE of the ways a client embeds a signature
        # image, and not the common one. Outlook and Zoho hang the logo off a Content-ID and
        # reference it from the HTML as <img src="cid:...">, with either no disposition at all
        # or (Outlook) "attachment" - so the inline check alone never sees it, and a footer
        # logo arrives as a .png with a filename, gets OCR'd, classified by the AI, and lands
        # on the job as a page nobody sent. Reported on 4S Logistics' own mail, whose footer
        # carries a company logo plus AEO, FTWZ, MSME and anniversary badges.
        #
        # Two signals, both conservative:
        #   * the cid is referenced in the HTML body - it is literally being displayed, so it
        #     is the letter, whatever its disposition claims
        #   * it has a Content-ID and is NOT explicitly marked "attachment" - a Content-ID
        #     exists in order to be referenced from the body; a genuine attachment from any
        #     ordinary client carries Content-Disposition: attachment
        #
        # A real document that happens to carry a Content-ID AND says "attachment" is still
        # kept, which is the case worth protecting: losing a customer's invoice would be far
        # worse than carrying a logo.
        cid = (part.get("Content-ID") or "").strip().strip("<>").lower()
        if cid and (cid in body_cids or "attachment" not in disp):
            continue
        fname = _decode(part.get_filename())
        if not fname:
            continue
        if Path(fname).suffix.lower() not in (exts or ATTACH_EXTS):
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


def _scratch_dir_name(mid: str) -> str:
    """The folder one message's attachments and page images are worked on in, unique per
    Message-ID.

    It used to be the first 60 letters/digits of the Message-ID alone. A pre-alert split into
    shipments gives each one the parent's id with its HAWB at the END, and a long Exchange id
    cut at 60 characters lost that ending - so two shipments processed at the same time shared
    one folder, overwrote each other's PDF and page images, and one crashed ("cannot save with
    zero pages"; JOB-070575 / JOB-70EF19 on production, 17 Nov 2026). A short fingerprint of
    the WHOLE id now makes every message's folder its own; the readable start is kept so a
    folder can still be traced back to its mail.
    """
    import hashlib

    readable = "".join(ch for ch in mid if ch.isalnum() or ch in "-_")[:40] or "msg"
    return f"{readable}-{hashlib.sha1(mid.encode('utf-8', 'replace')).hexdigest()[:12]}"


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


def _body_html_as_text(msg: email.message.Message) -> str:
    """The HTML half of the message as plain words.

    Only used to look for an ETA. Some senders' pre-alert detail survives only in the HTML
    part - the text/plain alternative their mail client generated is the forwarding wrapper
    and nothing else - and _body_text above deliberately returns the FIRST text/plain part,
    which is that wrapper.
    """
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        try:
            html = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
        html = re.sub(r"<[^>]+>", " ", html)
        for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
            html = html.replace(a, b)
        return "\n".join(" ".join(line.split()) for line in html.splitlines())
    return ""


def _eta_for_new_job(msg: email.message.Message, subject: str,
                     tenant_id: str | None) -> str | None:
    """The ETA this pre-alert carries, or None. Never raises: a job must be created whatever
    this does, and a missing ETA is an empty box an operator fills in, not a failure."""
    try:
        from app.core.email_eta import extract_eta

        found = extract_eta(subject, _body_text(msg), html=_body_html_as_text(msg),
                            tenant_id=tenant_id)
        if found:
            logger.info("ETA %s read off the pre-alert %r", found, (subject or "")[:60])
        return found
    except Exception:  # noqa: BLE001
        logger.exception("could not read an ETA off the pre-alert")
        return None


def _candidate_groups(db: Session, tenant_id: str | None) -> list[TemplateGroup]:
    """Every customer this pull is allowed to route to.

    Note what is NOT here: a filter on pull_email. Which customer a document belongs to is read
    off the document, so a customer with no address configured is still a candidate - that is
    exactly the case (a first-time sender) the old address matching could never handle.

    `tenant_id` scopes a mailbox-specific pull to that ONE mailbox's own assigned tenant (see
    _connected_mailboxes) - the shared inbox passes whatever the caller's own tenant filter is
    (None = every tenant), since it is never assigned to one tenant itself.

    Used to also narrow further by an operator's own UserTemplateAssignment rows, back when a
    mailbox was owned by one operator user - mailboxes are their own thing now (see
    ConnectedMailbox), each with its own tenant_id/group_id assigned directly, so that
    narrowing has moved to the caller (_pull_one_mailbox's own forced_group_id) rather than
    living here.
    """
    query = db.query(TemplateGroup)
    if tenant_id:
        query = query.filter(TemplateGroup.tenant_id == tenant_id)
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
    from app.core.classifier_common import IDENTIFYING_FIELDS
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

# Well under CLAIM_STALE_MINUTES, so a message that is genuinely still being worked never
# goes stale purely from taking a while. Found live: the SAME email turned into two separate
# jobs 8.5 minutes apart - its classification ran into heavy OpenAI rate-limit retries (see
# create_chat_completion_with_retry's own pool-wide retry/failover), which alone can push one
# message's processing past 30 minutes with nothing actually wrong, and a second poll saw the
# now-30-minutes-old claim, assumed the first run had crashed, and started a fresh one. Both
# eventually finished and each wrote its own job.
_CLAIM_HEARTBEAT_SECONDS = 180


def _start_claim_heartbeat(message_id: str) -> None:
    """Keeps a message's claim looking fresh for as long as _process_one_message is genuinely
    still working it - self-terminating, not externally signalled: every beat re-reads this
    message's own verdict, and stops the moment it is anything other than "working" (meaning
    _record() already wrote the real outcome - success or failure, it no longer matters). A
    run that actually crashes simply stops beating, and CLAIM_STALE_MINUTES of real silence
    still recovers it exactly as before - this only protects a run that is slow, not dead."""
    def _beat() -> None:
        from app.db.session import SessionLocal

        while True:
            time.sleep(_CLAIM_HEARTBEAT_SECONDS)
            own = SessionLocal()
            try:
                row = own.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
                if row is None or row.verdict != "working":
                    return
                row.updated_at = datetime.now(timezone.utc)
                own.commit()
            except Exception:  # noqa: BLE001
                return
            finally:
                own.close()

    threading.Thread(target=_beat, daemon=True, name="claim-heartbeat").start()


_pull_lock = threading.Lock()


# ---- when the AI service is down -------------------------------------------------------
#
# A mail whose reading fails because the AI service refused the call (no credits, rate limit,
# an outage) used to have its claim deleted so "the next poll tries again from scratch". With a
# one-minute poll and an account out of credits, that was the same 39 mails every minute for
# hours: each round created a job, OCR'd every page, WROTE THE PAGES TO THE BILL, failed at the
# first model call and deleted the job. Production, 16 Nov 2026: about 1,600 jobs made and
# removed in four hours and some 10,900 pages on the customer's bill for jobs that never
# existed - shown on the Billing page as "Deleted Jobs / Deleted Pages", because a billed job
# that no longer exists is all that page can see.
#
# Now:
#   - the first such failure starts a WAIT (AI_DOWN_WAIT_MINUTES). Until it ends no mail is
#     opened at all - nothing is OCR'd, no job is made, nothing is billed;
#   - every mail that failed, or arrived while waiting, is RECORDED (EmailSeen verdict
#     "failed") so a Super Admin can see exactly which mails are held up and why;
#   - when the wait ends the held mails are tried again by the ordinary poll. If the service is
#     still down the first few fail, the wait starts again, and the rest are not touched;
#   - a Super Admin can end the wait and re-run them at once (rerun_failed_emails) - after
#     adding credits, say;
#   - the pages of an attempt that never became a job come off the bill with the job.
AI_DOWN_WAIT_MINUTES = 15
# How many held mails one poll looks up by id, per mailbox - one IMAP search each.
HELD_LOOKUP_LIMIT = 300
FAILED_VERDICT = "failed"
_ai_down = {"until": None, "reason": ""}
_ai_down_lock = threading.Lock()


def ai_wait_status() -> dict:
    """{"waiting": bool, "until": iso | None, "reason": str} - is the puller holding off
    because the AI service refused its last call."""
    with _ai_down_lock:
        until, reason = _ai_down["until"], _ai_down["reason"]
    waiting = until is not None and datetime.now(timezone.utc) < until
    return {"waiting": waiting, "until": until.isoformat() if waiting else None,
            "reason": reason if waiting else ""}


def _note_ai_down(reason: str) -> None:
    with _ai_down_lock:
        already = _ai_down["until"] is not None and datetime.now(timezone.utc) < _ai_down["until"]
        _ai_down["until"] = datetime.now(timezone.utc) + timedelta(minutes=AI_DOWN_WAIT_MINUTES)
        _ai_down["reason"] = (reason or "")[:500]
    if not already:
        # Once per outage, not once per held mail: this is the line an alert can be keyed on.
        logger.error("AI SERVICE UNAVAILABLE - mail reading paused for %d min: %s",
                     AI_DOWN_WAIT_MINUTES, (reason or "")[:300])


def clear_ai_wait() -> None:
    with _ai_down_lock:
        _ai_down["until"] = None
        _ai_down["reason"] = ""


def _record_failed(db: Session, message_id: str, sender: str, subject: str, note: str,
                   group_id: str | None = None) -> None:
    """Write down that this mail is held up by the AI service - WITHOUT disturbing a mail that
    already has a real outcome. Only a mail never seen before, or one already marked failed,
    is written; a matched / unrouted / duplicate mail keeps its own record."""
    try:
        row = db.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
        if row is not None and row.verdict not in (FAILED_VERDICT, "working"):
            return
        if row is None:
            row = EmailSeen(message_id=message_id)
            db.add(row)
        row.sender = (sender or "")[:320] or None
        row.subject = (subject or "")[:500] or None
        row.verdict = FAILED_VERDICT
        row.note = (note or "")[:2000] or None
        row.group_id = group_id or row.group_id
        row.job_id = None
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not record the failed pull for %s", message_id[:60])
        db.rollback()


def hold_emails(db: Session, items: list[dict], note: str) -> dict:
    """Put mails on the held list by Message-ID - for mails that failed BEFORE the held list
    existed (the puller then deleted its own record of them), taken from a listing of the
    mailbox. A mail that already has a real outcome is left exactly as it is; only one never
    recorded, or already held, is written. Returns {"held": n, "left_alone": n}."""
    held = left = 0
    for item in items:
        message_id = str(item.get("message_id") or "").strip()
        if not message_id:
            continue
        row = db.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
        if row is not None and row.verdict not in (FAILED_VERDICT,):
            left += 1
            continue
        _record_failed(db, message_id, str(item.get("sender") or ""), str(item.get("subject") or ""), note)
        held += 1
    return {"held": held, "left_alone": left}


INTERRUPTED_NOTE = "Interrupted by a server restart - it will be read again automatically."
# Only a claim touched this recently is handed back. A restart interrupts what was being read
# minutes ago; a claim left from weeks ago (SIT had nine from August-October) is not something
# to start reading now - that would turn months-old mail into new jobs, billed pages and AI cost.
INTERRUPTED_WINDOW_HOURS = 24


def release_interrupted_claims(db: Session) -> list[str]:
    """At startup, hand back every mail the previous process was in the middle of reading.

    A claim says "being read" (verdict "working") until the reading ends. A restart, deploy or
    crash ends the process mid-read and leaves the claim saying so for ever. The 30-minute
    takeover in _claim only helps if the mail is fetched again - and the routine poll fetches
    only TODAY'S unread mail, so a mail cut off on an earlier day (or opened by someone since)
    was never read again: it sat at "being read" with no job (the DHL / STANLEY mail of
    2026-11-20).

    Called once, before the first poll, in the one process that polls: nothing of THIS process
    is reading yet, so every "working" claim belongs to a run that no longer exists. Each becomes
    a held mail (FAILED_VERDICT) - the existing path then fetches it again by its own
    Message-ID, whatever its date, and _claim lets it be read. Only claims touched within
    INTERRUPTED_WINDOW_HOURS; older ones are left exactly as they are. Returns the subjects released."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=INTERRUPTED_WINDOW_HOURS)

    def _touched(r: EmailSeen) -> datetime | None:
        t = r.updated_at or r.created_at
        # Stored as UTC without a timezone on SQLite (see _claim) - treat naive as UTC.
        return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t

    rows = [r for r in db.query(EmailSeen).filter(EmailSeen.verdict == "working").all()
            if (_touched(r) or now) >= cutoff]
    if not rows:
        return []
    released: list[str] = []
    for row in rows:
        label = (row.subject or row.message_id)[:80]
        job = db.get(Job, row.job_id) if row.job_id else None
        # Judged by STATUS, not stage_override: "Document Classifying" is set part-way through,
        # and the screen shows it from in-memory progress before then (SIT, 2026-11-21: a job
        # cut off mid-read still had stage_override None and was wrongly taken as finished).
        # A job still in "draft" has not got past reading; anything later has.
        if job is not None and job.status != "draft":
            # Its job got past reading before the restart (the cut came at the very end):
            # the mail is done - reading it again would only make a second job.
            row.verdict = "matched"
            row.note = "Finished just before a server restart."
            row.updated_at = now
            db.commit()
            continue
        if job is not None:
            # Cut off while reading: the half-made job (created the moment the mail arrived,
            # see _process_one_message) goes, with any pages already billed to it, so the
            # re-read makes ONE clean job instead of a second one beside a stuck shell.
            _discard_pre_created_job(db, job, unbill=True)
            row = db.query(EmailSeen).filter(EmailSeen.id == row.id).one()
        row.verdict = FAILED_VERDICT
        row.note = INTERRUPTED_NOTE
        row.job_id = None
        row.updated_at = now
        db.commit()
        released.append(label)
    return released


def failed_emails(db: Session) -> list[EmailSeen]:
    """Every mail currently held up because the AI service failed, oldest first."""
    return (db.query(EmailSeen).filter(EmailSeen.verdict == FAILED_VERDICT)
            .order_by(EmailSeen.created_at).all())


def rerun_failed_emails(db: Session) -> int:
    """End the wait and pull again now, on a thread of its own (a pull takes minutes; the
    button must not). Returns how many mails were waiting. Nothing is re-read here - the held
    mails are simply claimable again, and the pull that starts does what every poll does."""
    from app.db.session import SessionLocal

    clear_ai_wait()
    waiting = db.query(EmailSeen).filter(EmailSeen.verdict == FAILED_VERDICT).count()

    def _run() -> None:
        own = SessionLocal()
        try:
            pull_inbox(own, skip_if_busy=True)
        except Exception:  # noqa: BLE001
            logger.exception("re-run of the failed emails crashed")
        finally:
            own.close()

    threading.Thread(target=_run, daemon=True, name="email-rerun-failed").start()
    return waiting


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
    # The BL this subject announces, stored on the row itself - it is what makes the
    # duplicate check one indexed lookup instead of opening the attachments to find out what
    # the mail is about. None for most mail, which simply never takes part in the check.
    from app.core.email_bl import extract_bl_number

    bl_number = extract_bl_number(subject)
    row = EmailSeen(message_id=message_id, sender=sender[:320] or None,
                    subject=(subject or "")[:500] or None, verdict="working",
                    note="being read now", bl_number=bl_number)
    try:
        db.add(row)
        db.commit()
        return True, ""
    except IntegrityError:
        db.rollback()
    existing = db.query(EmailSeen).filter(EmailSeen.message_id == message_id).one_or_none()
    if existing is None:      # deleted between the insert and the read - let the next poll have it
        return False, "claimed by another run"
    # A mail held up by the AI service (verdict "failed") was never actually examined - it is
    # always claimable again, by an ordinary poll once the wait is over or by a Super Admin's
    # re-run. Every other finished verdict stays closed unless `steal` (reexamine) asks.
    if existing.verdict not in ("working", FAILED_VERDICT) and not steal:
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
    existing.bl_number = bl_number or existing.bl_number
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
    reexamine: bool = False, skip_if_busy: bool = False, use_queue: bool = False,
) -> dict:
    """Read unread mail, decide from the DOCUMENTS whose it is, and create the job.

    `tenant_id` limits to one tenant's customers. `reexamine=True` ignores the "already
    examined" record, for when a customer has just been configured and their earlier mail
    should be looked at again.

    `skip_if_busy` is for the BACKGROUND poller only, so its timer cycles cannot stack up on a
    slow mailbox. A person pressing Check mail is never turned away: correctness comes from
    _claim(), which hands each message to exactly one run, so a second run alongside the poller
    is safe - it simply skips whatever is already being read and reports on the rest. Blocking
    it instead told an operator "try again later" and did nothing, which is a worse answer than
    "no new mail" and was never needed to stop the duplicate.

    `use_queue` is for the background poller only: with MAIL_QUEUE on, harvested mail is put on
    the Redis mail queue for the mail workers instead of being read in this call (see
    app/core/mail_queue.py). A person pressing Check mail still waits for the result.
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
            return _pull_inbox(db, tenant_id, max_messages, reexamine, use_queue)
        finally:
            _pull_lock.release()
    return _pull_inbox(db, tenant_id, max_messages, reexamine, use_queue)


def _pull_inbox(
    db: Session, tenant_id: str | None = None, max_messages: int = 30,
    reexamine: bool = False, use_queue: bool = False,
) -> dict:
    """Poll every mailbox this call is allowed to read, and merge the results: the tenant's
    shared inbox, plus every Connected Mailbox assigned to a tenant (see
    _connected_mailboxes) - each carrying its OWN tenant/template assignment now, independent
    of any operator. Held under _pull_lock by its caller.

    Two phases, not one thread per mailbox for its whole backlog:

      1. HARVEST every mailbox, one thread per mailbox. Cheap - an IMAP search and a PEEK
         fetch, not OCR - so there is no reason to share this step.
      2. PROCESS every harvested message - OCR plus the AI calls, minutes of work each -
         through ONE SHARED pool, not one thread pinned to each mailbox. A worker that
         finishes a quiet mailbox's one message does not then sit idle while a busier
         mailbox still has nineteen more waiting; it picks up the next one off the SAME
         queue, whichever mailbox it came from. That is the actual fix: before this, a
         mailbox with one message and a mailbox with twenty each got exactly one dedicated
         worker regardless, so the quiet one's worker was wasted the moment it finished.

    _claim() is still the only thing standing between two workers and the same message - see
    its own docstring. This does not add a second lock: the task list handed to the shared
    pool in phase 2 already contains each harvested message exactly once, so there is nothing
    for a lock to arbitrate WITHIN one call to this function. _claim() remains the backstop
    for everything outside that one list - a manual "Pull email now" running at the same
    time as the scheduler, or reexamine re-walking mail this very cycle is also mid-way
    through - and if a collision ever does happen there, _claim() hands it to exactly one of
    them and the other moves on, which is already proven by its own tests.
    """
    from app.core.custom_page_filter import get_active_custom_filter_texts
    from app.core.system_settings import get_email_poll_workers, get_extraction_engine
    from app.db.session import SessionLocal

    settings = get_settings()
    sources: list[dict] = []
    if settings.gmail_user and _app_password():
        sources.append({"mailbox_id": None, "email": None, "password": None, "host": None,
                        "tenant_id": tenant_id, "group_id": None})
    sources.extend(_connected_mailboxes(db, tenant_id))

    if not sources:
        return {"ok": True, "processed": [], "note": "No mailbox is configured yet."}

    # ---- phase 1: harvest every mailbox concurrently, one thread per mailbox. Each thread
    # gets its OWN session - a SQLAlchemy session is not safe to share across threads - used
    # only for the per-mailbox setup (_mailbox_setup) and the per-mailbox settings this used
    # to read once at the top of _pull_one_mailbox; harvesting itself touches no DB.
    def _setup_and_harvest(src: dict) -> dict:
        worker_db = SessionLocal()
        try:
            ctx, early = _mailbox_setup(worker_db, src["tenant_id"], src["group_id"])
            if early is not None:
                return {"early": early}
            # Mails held up by the AI service are fetched by their own id as well (see
            # _harvest_mailbox) - not while still waiting, when nothing would be opened anyway.
            held: list[str] = []
            if not ai_wait_status()["waiting"]:
                try:
                    held = [r.message_id for r in failed_emails(worker_db)]
                except Exception:  # noqa: BLE001 - the routine poll must still run
                    logger.exception("could not read the held mails; polling without them")
                    worker_db.rollback()
            return {
                "ctx": ctx,
                "extraction_engine": get_extraction_engine(worker_db),
                "vision_engine_model": get_settings().vision_engine_model,
                "custom_filter_texts": get_active_custom_filter_texts(worker_db),
                # The held mails are passed only when there are any, so a poll with nothing
                # held makes exactly the call it always made.
                "harvest": _harvest_mailbox(
                    src["email"], src["password"], src["host"], max_messages, reexamine,
                    **({"held_message_ids": held} if held else {}),
                ),
            }
        finally:
            worker_db.close()

    mailbox_errors: list[str] = []
    combined_note: str | None = None
    tasks: list[dict] = []
    # {message id: {...}} for every pre-alert split into per-shipment tasks this cycle.
    split_parents: dict[str, dict] = {}

    harvest_workers = max(1, min(get_email_poll_workers(db), len(sources)))
    with ThreadPoolExecutor(max_workers=harvest_workers, thread_name_prefix="email-harvest") as pool:
        futures = {pool.submit(_setup_and_harvest, src): src for src in sources}
        for future in as_completed(futures):
            src = futures[future]
            label = src["email"] or "shared inbox"
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("mailbox harvest crashed for %s", label)
                mailbox_errors.append(f"{label}: {exc}")
                continue
            if "early" in result:
                early = result["early"]
                if not early.get("ok", True):
                    mailbox_errors.append(f"{label}: {early.get('error')}")
                elif early.get("note"):
                    combined_note = early["note"]
                continue
            harvest = result["harvest"]
            if not harvest.get("ok"):
                mailbox_errors.append(f"{label}: {harvest.get('error')}")
                continue
            ctx = result["ctx"]
            if ctx["unidentifiable"]:
                combined_note = ("These customers have nothing on their documents that "
                                 "identifies them, so mail can never be routed to them: "
                                 + ", ".join(ctx["unidentifiable"]))
            for message in harvest["messages"]:
                base = {
                    "src": src, "ctx": ctx, "uid": message["uid"], "raw": message["raw"],
                    "extraction_engine": result["extraction_engine"],
                    "vision_engine_model": result["vision_engine_model"],
                    "custom_filter_texts": result["custom_filter_texts"],
                }
                # One pre-alert covering several shipments becomes one TASK per shipment, right
                # here, so the pool below runs them side by side exactly as it would three
                # separate mails - see _split_into_shipment_tasks.
                shipment_tasks, parent = _split_into_shipment_tasks(db, base, reexamine)
                if parent is not None:
                    split_parents[parent["mid"]] = parent
                tasks.extend(shipment_tasks)

    # ---- phase 2: process every harvested message through ONE shared pool, sized by how
    # much work there actually is across every mailbox combined - not by how many mailboxes
    # it came from. A crash while processing one message is that message's own problem, not
    # the mailbox's - caught inside _process_with_own_session so it cannot take any sibling
    # message down with it, including ones from the very same mailbox now running on a
    # different worker.
    all_processed: list[dict] = []
    queued = 0
    if tasks and use_queue:
        from app.core import mail_queue

        if mail_queue.enabled():
            tasks, queued = _queue_harvested_mail(db, tasks, reexamine)
    if tasks:
        def _process_with_own_session(task: dict) -> dict | None:
            worker_db = SessionLocal()
            try:
                return _process_one_message(
                    worker_db, task["uid"], task["raw"], task["ctx"],
                    task["src"]["email"], task["src"]["password"], task["src"]["host"],
                    task["extraction_engine"], task["vision_engine_model"],
                    task["custom_filter_texts"], reexamine,
                    # Passed only for a mail the task list actually looked at - every other
                    # call keeps the exact argument list it always had.
                    **({"split_checked": True} if task.get("split_checked") is True else {}),
                    **({"prealert_rec": task["prealert_rec"]} if task.get("prealert_rec") else {}),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("processing a harvested email crashed")
                label = task["src"]["email"] or "shared inbox"
                return {"from": None, "subject": None, "matched": None, "error": True,
                        "reason": f"internal error while processing a message from {label}: {exc}"}
            finally:
                worker_db.close()

        # A big consolidation mail on a template using the large-job lane goes to its own worker
        # and this cycle carries on without waiting for it (app/core/large_job_lane.py).
        tasks = _hand_large_mail_to_lane(db, tasks, reexamine, _process_with_own_session)

        process_workers = max(1, min(get_email_poll_workers(db), len(tasks)))
        with ThreadPoolExecutor(max_workers=process_workers, thread_name_prefix="email-pull") as pool:
            futures = {pool.submit(_process_with_own_session, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    entry = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("a message-processing task itself crashed")
                    mailbox_errors.append(f"message processing: {exc}")
                    entry = {"from": None, "subject": None, "matched": None, "error": True,
                             "reason": f"message processing: {exc}"} if task.get("parent_mid") else None
                    if entry is None:
                        continue
                if task.get("parent_mid") in split_parents:
                    split_parents[task["parent_mid"]]["outcomes"].append((task.get("mid"), entry))
                if entry is not None:
                    all_processed.append(entry)
                    all_processed.extend(entry.get("split_into") or [])
        for parent in split_parents.values():
            all_processed.append(_settle_split_parent(db, parent))

    out: dict = {
        "ok": True, "processed": all_processed,
        "mailboxes_total": len(sources),
        "mailboxes_ok": len(sources) - len(mailbox_errors),
    }
    if queued:
        out["queued"] = queued
    if combined_note and not mailbox_errors:
        out["note"] = combined_note
    if mailbox_errors:
        out["mailbox_errors"] = mailbox_errors
    return out


def _hand_large_mail_to_lane(db: Session, tasks: list[dict], reexamine: bool, run_task) -> list[dict]:
    """The tasks this cycle should process itself. A mail whose mailbox template has the
    large-job lane on (TemplateGroup.large_job_lane) and whose biggest zip is over the
    template's large_zip_mb is handed to the lane instead (app/core/large_job_lane.py) - the
    cycle does not wait for it. Everything else - and every case where anything is unclear - is
    returned unchanged, so it is processed here exactly as before.

    Only in the process that owns the singleton background work (where the lane runs); a
    manual re-examine and pre-alert split parts always stay in the cycle."""
    if reexamine or not tasks:
        return tasks
    try:
        from app.core.worker_lock import am_i_the_singleton_worker

        if not am_i_the_singleton_worker():
            return tasks
    except Exception:  # noqa: BLE001
        return tasks
    from app.core import large_job_lane

    groups: dict[str, TemplateGroup | None] = {}
    keep: list[dict] = []
    for task in tasks:
        gid = (task.get("src") or {}).get("group_id")
        if not gid or task.get("parent_mid") or task.get("prealert_rec"):
            keep.append(task)
            continue
        if gid not in groups:
            groups[gid] = db.get(TemplateGroup, gid)
        big = _large_mail(groups[gid], task)
        if big is None:
            keep.append(task)
            continue
        mid, subject, biggest = big
        # Already examined (it stays unread in the inbox on purpose, so every poll of the day
        # finds it again): leave it to the cycle, whose _claim() skips it at once - exactly as
        # before the lane. Only a new mail, or one held "failed" for a retry, goes to the lane.
        seen = db.query(EmailSeen.verdict).filter(EmailSeen.message_id == mid).first()
        if seen is not None and seen[0] not in (FAILED_VERDICT,):
            keep.append(task)
            continue
        size_mb = biggest / (1024 * 1024)
        if large_job_lane.submit_email(mid, f"{subject or mid} ({size_mb:.1f} MB)",
                                       lambda t=task: run_task(t)):
            logger.info("email %s: zip of %.1f MB - handed to the large-job lane", mid[:60], size_mb)
        else:
            logger.info("email %s: already waiting on the large-job lane", mid[:60])
    return keep


def _image_fingerprint(page_images: list, pages: list[int]) -> str:
    """OCR off only (OCR_ENGINE none/pdftext): a hash of the rendered pixels of a claim's pages,
    for the in-mail duplicate check when there is no text to fingerprint. "" = cannot tell."""
    from app.core.docai import ocr_off

    if not ocr_off() or not pages:
        return ""
    try:
        import hashlib

        import fitz

        by_n = {int(n): p for n, p in page_images}
        h = hashlib.sha256()
        for n in pages:
            path = by_n.get(int(n))
            if path is None or not Path(path).exists():
                return ""
            pix = fitz.Pixmap(str(path))
            h.update(f"{pix.width}x{pix.height}:".encode())
            h.update(pix.samples)
        return "img:" + h.hexdigest()
    except Exception:  # noqa: BLE001 - cannot tell is the safe answer
        return ""


def _large_mail(group, task: dict) -> tuple[str, str, int] | None:
    """(message id, subject, biggest zip bytes) when this mail is a large-job-lane mail: its
    template has the lane on and its biggest zip is over the template's large_zip_mb. None
    otherwise - including any doubt, so it is read the ordinary way."""
    if group is None or getattr(group, "large_job_lane", False) is not True:
        return None
    try:
        msg = email.message_from_bytes(task["raw"])
        biggest = max((len(b) for n, b in _extract_attachments(msg, {".zip"})
                       if n.lower().endswith(".zip")), default=0)
        mid = _message_id(msg)
        subject = _decode(msg.get("Subject"))
    except Exception:  # noqa: BLE001
        logger.exception("large-job lane: could not size a mail - processing it normally")
        return None
    if biggest <= (getattr(group, "large_zip_mb", 5) or 5) * 1024 * 1024:
        return None
    return mid, subject, biggest


def _queue_harvested_mail(db: Session, tasks: list[dict], reexamine: bool) -> tuple[list[dict], int]:
    """MAIL_QUEUE on: put each harvested mail on the Redis mail queue (app/core/mail_queue.py)
    for the mail workers. Returns (tasks this cycle must still read itself, how many queued).

    Read here, as before: the per-shipment parts of a split pre-alert (their parent is settled
    in this cycle) and anything that could not be queued. Skipped: a mail already examined or
    being read (it stays unread in the inbox, so every poll finds it again) - its claim would
    turn it away anyway - and a mail already waiting on the queue."""
    from app.core import mail_queue

    keep: list[dict] = []
    queued = 0
    groups: dict[str, TemplateGroup | None] = {}
    for task in tasks:
        if task.get("parent_mid"):
            keep.append(task)
            continue
        try:
            mid = _message_id(email.message_from_bytes(task["raw"]))
            if not reexamine:
                seen = db.query(EmailSeen.verdict).filter(EmailSeen.message_id == mid).first()
                if seen is not None and seen[0] != FAILED_VERDICT:
                    continue
            gid = (task.get("src") or {}).get("group_id")
            if gid and gid not in groups:
                groups[gid] = db.get(TemplateGroup, gid)
            large = bool(gid) and _large_mail(groups.get(gid), task) is not None
            if mail_queue.enqueue(task, reexamine=reexamine, large=large):
                queued += 1
                logger.info("email %s: queued on %s", mid[:60], "mail:large" if large else "mail:queue")
        except Exception:  # noqa: BLE001 - never lose a mail: read it in this cycle instead
            logger.exception("could not queue a harvested mail - reading it in this cycle")
            db.rollback()
            keep.append(task)
    return keep, queued


def _split_into_shipment_tasks(db: Session, base: dict, reexamine: bool) -> tuple[list[dict], dict | None]:
    """The task(s) for one harvested mail: ([tasks], parent-or-None).

    Normally just the mail itself. A pre-alert covering several shipments (on a mailbox whose
    template has prealert_shipments on) becomes one task PER SHIPMENT instead, so the shared
    pool runs them at the same time - each on its own worker and session, each claimed,
    duplicate-checked and turned into a job exactly like a mail that arrived on its own. Done
    in the worker instead (as it first was) the shipments queued behind one another: three
    jobs took fifteen minutes and the second did not even appear until the first was finished.

    The mail itself is CLAIMED here, so the next poll does not split it a second time while its
    shipments are still being read; _settle_split_parent writes its outcome once they are done.
    Any doubt - not claimable, not splittable, anything raising - hands back the ordinary single
    task, marked split_checked so the worker does not ask the model the same question again.
    """
    single_group = base["ctx"].get("single_group") if isinstance(base.get("ctx"), dict) else None
    # `is not True`, not falsiness: only a template with the switch really ON is ever looked at.
    if single_group is None or getattr(single_group, "prealert_shipments", False) is not True:
        return [base], None
    if ai_wait_status()["waiting"]:
        return [base], None                          # the worker holds it; no model call here
    claimed, mid = False, None
    try:
        from app.core import prealert_shipments as _ps

        msg = email.message_from_bytes(base["raw"])
        subject = _decode(msg.get("Subject")) or "(no subject)"
        body = max(_body_text(msg) or "", _body_html_as_text(msg) or "", key=len)
        files = _extract_attachments(msg)
        # Worth checking: several HAWBs found by the rules (subject or body), or - for any other
        # format - two or more attachments, in which case the model decides (see plan_split).
        if msg.get(_ps.SHIPMENT_HEADER) or not (_ps.may_cover_several(msg, subject, body) or len(files) >= 2):
            return [{**base, "split_checked": True}], None
        sender = parseaddr(msg.get("From"))[1].lower()
        mid = _message_id(msg)
        claimed, _why = _claim(db, mid, sender, subject, steal=reexamine)
        if not claimed:
            return [base], None                      # the worker reports why, as for any mail
        found: dict = {}
        subs = _ps.plan_split(msg, subject, body, files, tenant_id=single_group.tenant_id, found=found)
        if not subs:
            _unclaim(db, mid)
            # The single shipment the model already read, so the worker does not ask again.
            single = {"prealert_rec": found["single"]} if found.get("single") else {}
            return [{**base, "split_checked": True, **single}], None
        _start_claim_heartbeat(mid)
        tasks = [{**base, "raw": sub, "split_checked": True, "parent_mid": mid,
                  "mid": _message_id(email.message_from_bytes(sub))} for sub in subs]
        return tasks, {"mid": mid, "sender": sender, "subject": subject,
                       "group_id": single_group.id, "group_name": single_group.name,
                       "shipments": len(subs), "outcomes": []}
    except Exception:  # noqa: BLE001 - then it is one job, as it always was
        logger.exception("could not split a pre-alert into shipment tasks")
        db.rollback()
        # The claim taken above must go, or the worker finds the mail "being read now" and
        # skips it - the mail then sat unread until the claim went stale (found 2026-11-22).
        if claimed and mid:
            _unclaim(db, mid)
        return [{**base, "split_checked": True}], None


def _settle_split_parent(db: Session, parent: dict) -> dict:
    """Write down what became of a pre-alert that was split into shipment tasks.

    Every shipment finished (a job, or a deliberate outcome such as "duplicate - held for
    review"): the mail is recorded as split and never looked at again. Any shipment NOT
    finished - deferred because the AI service was down, crashed, or still held by another
    run - and the mail's own claim is released instead, so the next poll splits it again; the
    shipments that did finish are skipped then by their own records, and a crashed one has its
    claim released here so it is not left "being read" for ever.
    """
    pending: list = []
    made: list[str] = []
    for child_mid, entry in parent["outcomes"]:
        entry = entry or {}
        if entry.get("job_id"):
            made.append(entry["job_id"])
        unfinished = (entry.get("deferred") or entry.get("error")
                      or (entry.get("skipped_by_log")
                          and "already examined" not in str(entry.get("reason") or "")))
        if unfinished:
            pending.append(child_mid)
            if entry.get("error") and child_mid:
                _unclaim(db, child_mid)
    if pending or len(parent["outcomes"]) < parent["shipments"]:
        _unclaim(db, parent["mid"])
        return {"from": parent["sender"], "subject": parent["subject"], "matched": None,
                "reason": "a shipment in this pre-alert could not be processed yet - "
                          "retrying automatically on a later poll",
                "deferred": True}
    _record(db, parent["mid"], parent["sender"], parent["subject"], "split",
            f"pre-alert covering {parent['shipments']} shipments - {len(made)} job(s) created",
            group_id=parent["group_id"])
    return {"from": parent["sender"], "subject": parent["subject"], "matched": parent["group_name"],
            "reason": f"split into {parent['shipments']} shipments, {len(made)} job(s) created"}


def _mailbox_setup(
    db: Session, tenant_id: str | None, forced_group_id: str | None,
) -> tuple[dict | None, dict | None]:
    """Everything about pulling THIS mailbox that does not depend on any one message: which
    customers (groups) it may route into, and - for a shared mailbox serving several
    customers - how to recognise each one from their documents. Computed once per mailbox,
    reused for every message harvested from it (see _harvest_mailbox / _process_one_message),
    so a shared pool processing many of its messages concurrently never repeats this work.

    Returns (ctx, early_result) - exactly one is not None. early_result is a "nothing to do
    here" result (no customers configured at all, or none has anything identifying) in the
    exact shape _pull_one_mailbox used to return directly for these cases.
    """
    # A mailbox explicitly assigned to exactly ONE template (forced_group_id, set on the
    # Email Puller screen) has nowhere else its mail could possibly belong - unlike a shared
    # inbox serving several different customers at once (a forwarding agent mailing for six
    # importers from one address), there is no ambiguity here for the documents' content to
    # resolve. Every message is trusted to be for this one template directly, whether or not
    # the exporter/consignee on it has ever been configured as a known identifier - that
    # requirement only exists to arbitrate BETWEEN customers, and there is only one here.
    single_group = db.get(TemplateGroup, forced_group_id) if forced_group_id else None
    groups: list[TemplateGroup] = [single_group] if single_group else _candidate_groups(db, tenant_id)
    if not groups:
        return None, {"ok": True, "processed": [], "note": "No customers are configured yet."}

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
            return None, {
                "ok": True, "processed": [],
                "note": ("No customer has anything on their documents that identifies them "
                         "(a consignee name, IE code or GST number configured as a standing "
                         "value). Add one to a template and pull again."),
                "unidentifiable": unidentifiable,
            }

    ctx = {
        "single_group": single_group, "candidates": candidates,
        "by_id": {g.id: g for g in groups}, "unidentifiable": unidentifiable,
    }
    return ctx, None


def _harvest_mailbox(
    mail_email: str | None, mail_app_password: str | None, mail_host: str | None,
    max_messages: int, reexamine: bool, held_message_ids: list[str] | None = None,
) -> dict:
    """Connect to ONE mailbox just long enough to list and fetch its candidate messages, then
    disconnect. Returns the raw material, not a verdict - deciding whose documents these are
    (OCR, AI classification, building the job) is the expensive part, minutes per message, and
    is deliberately NOT done here - see _process_one_message, run later by whichever worker in
    a shared pool picks this message up next, not necessarily the thread that harvested it.
    That split is what lets an idle worker (its own mailbox already empty) help a busier
    mailbox's backlog instead of sitting out the rest of the poll cycle.
    """
    try:
        conn = _connect(mail_email, mail_app_password, mail_host)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "messages": []}
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
        # ...and every mail held up by the AI service (see AI_DOWN_WAIT_MINUTES), looked up by
        # its OWN Message-ID. The search above is "unread, arrived today": a mail that failed
        # yesterday evening is neither found by it after midnight nor, if someone opened it in
        # the meantime, unread - so without this a held mail would sit on the Super Admin's
        # list for ever and "Rerun all" would do nothing for it. A mailbox simply has no match
        # for an id that belongs to another mailbox.
        for held_id in (held_message_ids or [])[:HELD_LOOKUP_LIMIT]:
            try:
                typ2, found = conn.search(None, "HEADER", "Message-ID", '"%s"' % held_id.replace('"', ""))
            except Exception:  # noqa: BLE001 - one odd id must not cost the whole mailbox
                logger.warning("could not look up held message %s", held_id[:60])
                continue
            for uid in (found[0].split() if typ2 == "OK" and found and found[0] else []):
                if uid not in uids:
                    uids.append(uid)
        messages: list[dict] = []
        for uid in uids:
            # PEEK, so looking does not mark it read. Only a message that becomes a job does.
            typ, raw = conn.fetch(uid, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            messages.append({"uid": uid, "raw": raw[0][1]})
        return {"ok": True, "messages": messages}
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not harvest mailbox")
        return {"ok": False, "error": str(exc), "messages": []}
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _is_waybill_slot(slot: dict) -> bool:
    """The Air Waybill / Bill of Lading slot of a template (by its document type or name)."""
    dt = (slot.get("doc_type") or "").strip().upper().replace(" ", "")
    return dt in {"BL", "AWB", "HAWB", "MAWB", "AIRWAYBILL", "BILLOFLADING"} or "waybill" in (slot.get("name") or "").lower()


def _hold_for_password(db: Session, msg, mid: str, sender: str, subject: str, group: TemplateGroup,
                       raw_attachments: list[tuple[str, bytes]], notes: dict | None) -> dict:
    """Create the job as "Waiting for Password" - its slots empty, nothing read or billed - and
    keep the mail with it, so it can be read in full once the password is given (see
    resume_held_job). The operator sees it at once, in Missing Documents, with the locked zips
    and the file names inside them."""
    from app.api.v1.jobs import generate_job_no
    from app.core.job_email import save_original_email
    from app.core.zip_passwords import WAITING_FOR_PASSWORD

    job = Job(
        tenant_id=group.tenant_id, group_id=group.id, reference=generate_job_no(),
        status="draft", created_by_id=None, stage_override=WAITING_FOR_PASSWORD,
        assigned_operator_id=_operator_matching_sender(db, group.tenant_id, sender),
        eta_date=_eta_for_new_job(msg, subject, group.tenant_id),
        attachment_notes={**(notes or {}), "held": True},
    )
    db.add(job)
    db.flush()
    for tdoc in (db.query(TemplateDocument).filter(TemplateDocument.group_id == group.id)
                 .order_by(TemplateDocument.order_index).all()):
        db.add(JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                           template_document_id=tdoc.id, page_count=0))
    db.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id, status="draft", stage=WAITING_FOR_PASSWORD,
                    note="A zip in this mail is password-protected - waiting for the password "
                         "before reading anything."))
    db.commit()
    save_original_email(job.id, msg, raw_attachments, sender, subject,
                        received_by=None)
    locked = (notes or {}).get("locked") or []
    _record(db, mid, sender, subject, "held_password",
            f"{len(locked)} password-protected zip(s) - waiting for the password",
            group_id=group.id, job_id=job.id)
    logger.info("email %s: held as %s - %d password-protected zip(s)", mid[:60], job.reference, len(locked))
    return {"from": sender, "subject": subject, "matched": group.name, "job_id": job.id,
            "reason": "waiting for a zip password", "held": True}


def resume_held_job(job_id: str, *, password: str | None = None, skip_locked: bool = False) -> dict | None:
    """Read a held job's mail in full, into that same job: with `password` (already checked
    by the caller) or, with `skip_locked`, without the locked zips. Runs in its own session -
    called on a background thread from the job's "Enter password" / "Process without locked
    files" buttons."""
    from app.core.job_email import job_email_dir
    from app.core.system_settings import get_extraction_engine
    from app.core.custom_page_filter import get_active_custom_filter_texts
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return None
        raw = (job_email_dir(job.id) / "original.eml").read_bytes()
        ctx, early = _mailbox_setup(db, job.tenant_id, forced_group_id=job.group_id)
        if early is not None:
            return early
        return _process_one_message(
            db, b"", raw, ctx, None, None, None,
            get_extraction_engine(db), get_settings().vision_engine_model,
            get_active_custom_filter_texts(db), True,
            resume_job_id=job.id, extra_passwords=[password] if password else [],
            skip_locked=skip_locked,
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not resume held job %s", job_id)
        db.rollback()
        from app.core.zip_passwords import WAITING_FOR_PASSWORD

        job = db.get(Job, job_id)
        if job is not None and job.stage_override in (None, "Document Classifying"):
            job.stage_override = WAITING_FOR_PASSWORD
            db.commit()
        return None
    finally:
        db.close()


def _discard_pre_created_job(db: Session, job: Job | None, unbill: bool = False) -> None:
    """Undoes the early job + empty-slots creation a single-template mailbox gets (see
    _process_one_message's own docstring) when the message turns out not to produce a real
    job after all - OCR found nothing readable, the tenant's licensed modes no longer allow
    this template, or the AI service itself was unavailable mid-run. A job that briefly
    existed and then vanished is a little confusing to have glimpsed, but far less so than a
    duplicate from a later retry, or a permanently empty shell nobody ever asked for. A
    shared mailbox never has anything to discard here - job is None until classification
    actually identifies the customer, for exactly that path.
    """
    if job is None:
        return
    email_progress.clear(job.id)
    if getattr(job, "_resumed_from_hold", False):
        # A job that was waiting for a zip password and has just been given one: it is the
        # operator's job, already on their list - never deleted. Back to waiting, unbilled.
        from app.core.zip_passwords import WAITING_FOR_PASSWORD
        from app.models.billable_page_event import BillablePageEvent

        db.rollback()
        live = db.get(Job, job.id)
        if live is not None:
            db.query(BillablePageEvent).filter(BillablePageEvent.job_id == live.id).delete(
                synchronize_session=False)
            live.stage_override = WAITING_FOR_PASSWORD
            db.commit()
        return
    if unbill:
        # The pages were written to the bill the moment they were OCR'd, before the model was
        # ever asked anything. The job they were billed against is about to stop existing and
        # the mail will be read again from the start - leaving them would bill every retry of
        # the same mail, against a job nobody was ever given.
        from app.models.billable_page_event import BillablePageEvent

        db.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).delete(
            synchronize_session=False)
    for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
        db.delete(jd)
    db.delete(job)
    db.commit()


def _file_prealert_awb(db: Session, group_id: str, job: Job, slots: dict, shipment_for,
                       filled: list[str], named: list[str]) -> bool:
    """File a generated "Air Waybill (Pre-Alert)" in this job's waybill slot when no waybill
    arrived with the mail. Returns True when one was filed.

    `shipment_for` is a callable giving the pre-alert's shipment record (or None) - called only
    once the slot is known to be empty, so a mail that DID bring a waybill never costs a model
    call.

    The waybill document is looked up with a QUERY on group_id, never through
    TemplateGroup.documents: the group the puller holds was loaded once per mailbox on another
    session and is shared by every worker, so a lazy load off it raises DetachedInstanceError.
    That is exactly how the first real mail (JOB-703FCC) was split correctly and then got no
    waybill.
    """
    from app.api.v1.jobs import _job_doc_dir, _render_pages
    from app.core import prealert_shipments as _ps

    awb_tdoc = (
        db.query(TemplateDocument)
        .filter(TemplateDocument.group_id == group_id, TemplateDocument.doc_type == "BL")
        .order_by(TemplateDocument.order_index)
        .first()
    )
    awb_slot = slots.get(awb_tdoc.id) if awb_tdoc is not None else None
    if awb_slot is None or awb_slot.file_path:
        return False
    shipment = shipment_for()
    if not shipment:
        return False
    fname = _ps.awb_filename(shipment)
    ddir = _job_doc_dir(awb_slot.id)
    ddir.mkdir(parents=True, exist_ok=True)
    dorig = ddir / fname
    dorig.write_bytes(_ps.build_awb_pdf(shipment))
    awb_slot.page_count = _render_pages(dorig, ddir / "pages")
    awb_slot.file_path = str(dorig)
    awb_slot.original_name = fname
    if awb_tdoc.name not in filled:
        filled.append(awb_tdoc.name)
    named.append(f"(generated from the pre-alert) -> {fname}")
    db.commit()
    logger.info("job %s: Air Waybill generated from the pre-alert (HAWB %s)",
                job.id, shipment.get("hawb"))
    return True


def _process_one_message(
    db: Session, uid: bytes, raw: bytes, ctx: dict,
    mail_email: str | None, mail_app_password: str | None, mail_host: str | None,
    extraction_engine: str, vision_engine_model: str, custom_filter_texts: list,
    reexamine: bool, split_checked: bool = False, prealert_rec: dict | None = None,
    resume_job_id: str | None = None, extra_passwords: list[str] | tuple = (),
    skip_locked: bool = False,
) -> dict | None:
    """Everything _pull_one_mailbox's own loop used to do for ONE message, given its
    already-harvested raw bytes (see _harvest_mailbox) instead of a live, mailbox-owned IMAP
    connection - any worker in a shared pool may run this for any mailbox's message, not only
    the thread that harvested it (see _pull_inbox). Returns None for the one case the original
    loop used to `continue` past without reporting anything at all; every other outcome is a
    dict exactly as before.

    Correctness against two workers somehow being handed the same message is still entirely
    _claim()'s job - see its own docstring. Nothing here adds a second locking mechanism: a
    caller's task list already hands each harvested message to exactly one call of this
    function, and _claim() is the backstop for everything outside that one list (a manual
    "Pull email now" running alongside the scheduler, or reexamine re-walking mail the
    scheduler is also mid-way through).
    """
    from app.api.v1.jobs import _job_doc_dir, _render_pages, generate_job_no, run_extraction
    from app.core.classifier_common import AIServiceUnavailable, classifier_module, identify_customer
    from app.core.custom_page_filter import filter_pages_with_custom
    from app.core.docai import ocr_page_image, save_text_layer
    from app.core import doc_sets
    from app.core.page_filter import (
        claim_covers_file, claim_fingerprint, extract_pdf_pages,
        kept_page_to_original,
    )

    single_group = ctx["single_group"]
    candidates = ctx["candidates"]
    by_id = ctx["by_id"]

    msg = email.message_from_bytes(raw)
    sender = parseaddr(msg.get("From"))[1].lower()
    subject = _decode(msg.get("Subject")) or "(no subject)"
    mid = _message_id(msg)

    # The AI service refused a call a moment ago: do not open this mail at all. Nothing is
    # downloaded, OCR'd, billed or turned into a job - it is written down as held up, and the
    # ordinary poll comes back to it when the wait ends. See AI_DOWN_WAIT_MINUTES.
    waiting = ai_wait_status()
    if waiting["waiting"]:
        _record_failed(db, mid, sender, subject,
                       "Waiting - the AI service is unavailable: " + waiting["reason"],
                       group_id=single_group.id if single_group is not None else None)
        return {"from": sender, "subject": subject, "matched": None,
                "reason": "held - the AI service is unavailable; it is retried automatically",
                "deferred": True}

    # Claim it BEFORE reading it, not after. See _claim(). A mail being RESUMED (a held job was
    # just given its zip password) is already owned by that job - not claimed, not split again,
    # not duplicate-checked again.
    resume_job = db.get(Job, resume_job_id) if resume_job_id else None
    if resume_job is not None:
        claimed, why_not, split_checked, reexamine = True, "", True, True
    else:
        claimed, why_not = _claim(db, mid, sender, subject, steal=reexamine)
    if not claimed:
        return {"from": sender, "subject": subject, "matched": None,
                "reason": why_not, "skipped_by_log": True}

    # ---- one pre-alert covering several shipments? (app/core/prealert_shipments.py)
    #
    # Only on a mailbox tied to ONE template that has the handling switched on
    # (TemplateGroup.prealert_shipments). BEFORE the duplicate-BL check and before anything is
    # downloaded or OCR'd: each shipment then goes through this very function as a mail of its
    # own - its own claim, its own duplicate check, its own job - exactly as if the sender had
    # written three mails instead of one. Nothing is sent anywhere.
    prealert_on = single_group is not None and bool(getattr(single_group, "prealert_shipments", False))
    if prealert_on:
        from app.core import prealert_shipments as _ps

        sub_messages = None
        # split_checked: the task list already looked at this mail (_split_into_shipment_tasks)
        # and either split it - this IS one of its shipments - or found nothing to split. The
        # in-worker split below is only for the one caller that hands mails over one at a time
        # (_pull_one_mailbox), where the shipments then run one after another.
        try:
            if split_checked:
                raise LookupError
            split_body = max(_body_text(msg) or "", _body_html_as_text(msg) or "", key=len)
            sub_messages = _ps.plan_split(msg, subject, split_body, _extract_attachments(msg),
                                          tenant_id=single_group.tenant_id)
        except LookupError:
            pass
        except Exception:  # noqa: BLE001 - then it is processed as one job, as it always was
            logger.exception("could not check %s for several shipments", mid[:60])
        if sub_messages:
            _start_claim_heartbeat(mid)
            children: list[dict] = []
            for sub_raw in sub_messages:
                try:
                    child = _process_one_message(
                        db, uid, sub_raw, ctx, mail_email, mail_app_password, mail_host,
                        extraction_engine, vision_engine_model, custom_filter_texts, reexamine,
                    )
                except Exception:  # noqa: BLE001 - one shipment must not lose the others
                    logger.exception("a shipment split from %s could not be processed", mid[:60])
                    db.rollback()
                    child = {"from": sender, "subject": subject, "matched": None,
                             "reason": "could not be processed", "deferred": True}
                if child is not None:
                    children.append(child)
            if any(c.get("deferred") for c in children):
                # Tried again on a later poll. The shipments that DID become jobs are skipped
                # then by their own claims, so nothing is created twice.
                _unclaim(db, mid)
                return {"from": sender, "subject": subject, "matched": None,
                        "reason": "a shipment in this pre-alert could not be processed yet - "
                                  "retrying automatically on a later poll",
                        "deferred": True, "split_into": children}
            made = [c["job_id"] for c in children if c.get("job_id")]
            _record(db, mid, sender, subject, "split",
                    f"pre-alert covering {len(sub_messages)} shipments - {len(made)} job(s) created",
                    group_id=single_group.id)
            return {"from": sender, "subject": subject, "matched": single_group.name,
                    "reason": f"split into {len(sub_messages)} shipments, {len(made)} job(s) created",
                    "split_into": children}

    # ---- the same BL, from the same sender, already seen this month?
    #
    # HERE, before a single attachment is downloaded and before any page is OCR'd. That is
    # the whole value of reading the BL off the SUBJECT: recognising a re-sent pre-alert
    # costs one indexed lookup, where letting it through costs an OCR pass over every page,
    # an AI classification and an extraction run - and leaves the operator two identical
    # jobs to tell apart. The message is kept whole, so a person who decides it really is a
    # separate shipment can still process it (app/core/duplicate_bl.py).
    from app.core.duplicate_bl import already_queued, find_original, record_duplicate
    from app.core.email_bl import extract_bl_number

    bl_number = extract_bl_number(subject)
    if bl_number and not reexamine:
        original = find_original(db, bl_number=bl_number, sender=sender)
        if original is not None:
            if not already_queued(db, mid):
                record_duplicate(db, message_id=mid, sender=sender, subject=subject,
                                 bl_number=bl_number, original=original, raw=raw)
            _record(db, mid, sender, subject, "duplicate_bl",
                    f"BL {bl_number} already came in from this sender - held for review")
            return {"from": sender, "subject": subject, "matched": None,
                    "reason": f"duplicate BL {bl_number}", "duplicate": True}

    # Keep this claim from looking stale to every OTHER worker/poll while this one is
    # genuinely still working it - see _start_claim_heartbeat's own docstring.
    if resume_job is None:
        _start_claim_heartbeat(mid)

    # ---- attachments, including whatever is inside a .zip
    import shutil
    import uuid

    from app.core import office_convert

    tmp = Path(get_settings().uploads_dir) / "_email" / _scratch_dir_name(mid)

    files: list[tuple[str, bytes]] = []
    # Word/Excel attachments are converted below, same as a manual Smart Upload already does -
    # collected separately here since they need that extra step before they can join `files`.
    office_attachments: list[tuple[str, bytes]] = []
    # Every readable file, including inside zips at any depth and zips inside zips - see
    # attachment_unpack. Same names and order as before for an ordinary mail; what could not
    # be read (a password-protected zip, a damaged one, a type we do not read) is kept as
    # notes for the job instead of being dropped without a trace.
    from app.core.attachment_unpack import unpack_attachments
    from app.core.excel_intake import intake_excel_files, is_excel, is_sheet_mode
    from app.core.excel_text import EXCEL_EXTS

    # A directly attached Excel file is only picked up on an Excel-by-sheet template; every other
    # template keeps reading PDF/image/zip attachments only, exactly as before.
    attach_exts = (ATTACH_EXTS | EXCEL_EXTS) if is_sheet_mode(single_group) else ATTACH_EXTS
    raw_attachments = _extract_attachments(msg, attach_exts)
    # The biggest zip as attached (compressed size) - for the template's "hold extraction for a
    # large zip" rule (TemplateGroup.large_zip_hold / large_zip_mb).
    biggest_zip = max((len(b) for n, b in raw_attachments if n.lower().endswith(".zip")), default=0)
    # Saved zip passwords for this customer (app/core/zip_passwords.py) - only looked up when a
    # zip is attached, and tried only if one of them is actually password-protected.
    from app.core.zip_passwords import WAITING_FOR_PASSWORD, candidate_passwords, mark_used

    saved_pw: list[tuple[str, str]] = []
    if any(n.lower().endswith(".zip") for n, _ in raw_attachments):
        try:
            for tid in sorted({g.tenant_id for g in by_id.values()}):
                saved_pw += candidate_passwords(db, tid, group_id=single_group.id if single_group else None,
                                                sender=sender)
        except Exception:  # noqa: BLE001 - no passwords is the old behaviour, never a crash
            logger.exception("could not load saved zip passwords")
            db.rollback()
    unpacked = unpack_attachments(raw_attachments, CLASSIFY_EXTS | office_convert.OFFICE_EXTS,
                                  passwords=list(extra_passwords) + [pw for _id, pw in saved_pw])
    if unpacked.used_passwords:
        mark_used(db, [rid for rid, pw in saved_pw if pw in unpacked.used_passwords])
        db.commit()
    attachment_notes = unpacked.notes()
    if unpacked.unlocked:
        attachment_notes = dict(attachment_notes or {})
        attachment_notes["unlocked"] = unpacked.unlocked

    # ---- a password-protected zip no saved password opens: HOLD the whole job, read nothing.
    # Big consolidations take a long time to read, so they are read ONCE, with everything in
    # them, once the password is given (on the job, or saved for the customer) - not half now
    # and half later. Nothing is OCR'd, classified or billed while it waits. Only on a mailbox
    # tied to one template, where the job's customer is already known.
    if unpacked.locked and not skip_locked and single_group is not None and resume_job is None:
        return _hold_for_password(db, msg, mid, sender, subject, single_group, raw_attachments,
                                  attachment_notes)
    # Main folder or sub-folder of a zip, per file in `files` (see the consolidation rule below).
    from app.core.attachment_unpack import root_flags

    _flags = root_flags(unpacked.paths)
    file_root: list[bool | None] = []
    for (name, blob), flag in zip(unpacked.files, _flags):
        if Path(name).suffix.lower() in CLASSIFY_EXTS:
            files.append((name, blob))
            file_root.append(flag)
        else:
            office_attachments.append((name, blob))
    # Excel by sheet (TemplateGroup.excel_mode = "sheet", on a mailbox tied to one template):
    # Excel files are NOT converted to PDF - each sheet is read from its cells, classified and
    # placed after the PDFs (app/core/excel_intake.py). Every other template: exactly as before.

    excel_files: list[tuple[str, bytes]] = []
    if is_sheet_mode(single_group):
        excel_files = [(n, b) for n, b in office_attachments if is_excel(n)]
        office_attachments = [(n, b) for n, b in office_attachments if not is_excel(n)]

    # Word -> one PDF, Excel -> one PDF per worksheet - the SAME conversion Smart Upload
    # already does for a manual upload (see its own identical block in jobs.py). Found live on
    # JOB-8616BC: an Excel/Word attachment arriving by email was silently dropped before
    # classification ever saw it - CLASSIFY_EXTS (PDF/PNG/JPG only) filtered it straight out
    # above, with no log line and no "unclassified" staging, so there was no trace it had even
    # arrived.
    if office_attachments:
        office_work_dir = tmp / "_office"
        office_work_dir.mkdir(parents=True, exist_ok=True)
        for name, data in office_attachments:
            ext = Path(name).suffix.lower()
            src = office_work_dir / f"{uuid.uuid4().hex}{ext}"
            src.write_bytes(data)
            try:
                if ext in office_convert.WORD_EXTS:
                    pdf = office_convert.convert_word_to_pdf(src, office_work_dir, office_work_dir)
                    files.append((f"{Path(name).stem}.pdf", pdf.read_bytes()))
                else:
                    for sheet_name, pdf in office_convert.convert_excel_sheets_to_pdfs(
                        src, office_work_dir, office_work_dir
                    ):
                        files.append((f"{Path(name).stem} - {sheet_name}.pdf", pdf.read_bytes()))
            except office_convert.OfficeConversionFailed as exc:
                logger.warning(
                    "email classify: %s - could not convert this Office attachment: %s",
                    name, exc,
                )

    # The shipment this pre-alert describes, for building its Air Waybill when none is attached
    # (see prealert_shipments.build_awb_pdf). A split mail carries its own record; an ordinary
    # single-shipment mail is read by the model only if and when a waybill turns out to be
    # needed, so the common case costs nothing.
    # Already read by the task list's split check (plan_split's `found`), when it was asked.
    _prealert_cache: dict = {"rec": prealert_rec} if prealert_rec and prealert_rec.get("hawb") else {}

    def _prealert_shipment() -> dict | None:
        if not prealert_on:
            return None
        if "rec" not in _prealert_cache:
            rec = _ps.shipment_from_message(msg)
            if rec is None:
                try:
                    named_hawbs = _ps.hawbs_from_subject(subject)
                    if len(named_hawbs) <= 1:
                        body_text = max(_body_text(msg) or "", _body_html_as_text(msg) or "", key=len)
                        found = _ps.read_shipments(subject, body_text, named_hawbs,
                                                   tenant_id=single_group.tenant_id)
                        rec = found[0] if len(found) == 1 else None
                except Exception:  # noqa: BLE001
                    logger.exception("could not read the shipment off pre-alert %s", mid[:60])
                    rec = None
            _prealert_cache["rec"] = rec if rec and rec.get("hawb") else None
        return _prealert_cache["rec"]

    if not files and not excel_files and _prealert_shipment():
        # Nothing attached, but the mail itself says what the shipment is: its generated Air
        # Waybill becomes the job's document instead of the mail being thrown back.
        files.append((_ps.awb_filename(_prealert_shipment()), _ps.build_awb_pdf(_prealert_shipment())))
        logger.info("pre-alert %s has no attachments - Air Waybill generated from its data", mid[:60])

    if not files and not excel_files:
        what = unpacked.summary()
        _record(db, mid, sender, subject, "no_documents",
                "nothing readable was attached" + (f" ({what})" if what else ""))
        return {"from": sender, "subject": subject, "matched": None,
                "reason": "no document attachments" + (f" ({what})" if what else "")}

    # ---- read every page, and hold the text only as long as it is needed
    doc_texts: list[tuple[str, str]] = []
    prepared: list[dict] = []
    group = None
    filled: list[str] = []
    # {slot key: [text of each PDF placed there]} - for the Excel-vs-PDF duplicate check.
    placed_pdf_texts: dict[str, list[str]] = {}
    named: list[str] = []
    who: dict = {}
    tie_note = ""
    job = None
    slots: dict[str, JobDocument] = {}
    slot_meta: dict[str, dict] = {}
    pull_started_at = time.time()
    try:
        import fitz  # PyMuPDF

        # A mailbox dedicated to exactly one template already knows its tenant and group
        # before a single byte of OCR has run - create the job and its empty document slots
        # immediately, so it shows up on the operator's own Jobs list right away instead of
        # only once OCR and classification finish, which for a several-attachment email is
        # minutes away. A shared mailbox cannot do this - which customer this belongs to is
        # the very thing OCR + classification below are about to work out.
        if single_group is not None:
            group = single_group
            held_slots: dict[str, JobDocument] = {}
            if resume_job is not None:
                # The held job, given its password: read into IT, not into a new one.
                job = resume_job
                job._resumed_from_hold = True
                job.stage_override = None
                for jd_row in (db.query(JobDocument).filter(JobDocument.job_id == job.id)
                               .order_by(JobDocument.file_index).all()):
                    held_slots[jd_row.template_document_id] = jd_row
            else:
                job = Job(
                    tenant_id=group.tenant_id, group_id=group.id, reference=generate_job_no(),
                    status="draft", created_by_id=None,
                    assigned_operator_id=_operator_matching_sender(db, group.tenant_id, sender),
                    # Straight off the mail that created it - see _eta_for_new_job. Operations
                    # typed this in by hand on every job, from an email that already said it.
                    eta_date=_eta_for_new_job(msg, subject, group.tenant_id),
                )
                db.add(job)
                db.flush()
            for tdoc in (
                db.query(TemplateDocument)
                .filter(TemplateDocument.group_id == group.id)
                .order_by(TemplateDocument.order_index)
                .all()
            ):
                jd = held_slots.get(tdoc.id)
                if jd is None:
                    jd = JobDocument(tenant_id=group.tenant_id, job_id=job.id,
                                     template_document_id=tdoc.id, page_count=0)
                    db.add(jd)
                slots[tdoc.id] = jd
                slot_meta[tdoc.id] = {
                    "key": tdoc.id, "name": tdoc.name, "doc_type": tdoc.doc_type,
                    "fields": [m.label_name for m in tdoc.marks],
                    "reference": _slot_reference(tdoc.id),
                }
            # A real commit, not just a flush - a flush is only visible inside THIS
            # transaction; the operator's own browser is polling through a completely
            # different connection, and a job it can never actually see yet is no better
            # than the delay this whole feature exists to remove.
            # The claim learns its job NOW, not only at the end: if a restart cuts this read
            # off, release_interrupted_claims must know which half-made job belongs to this
            # mail - otherwise the re-read makes a second job beside it.
            db.query(EmailSeen).filter(EmailSeen.message_id == mid).update(
                {"job_id": job.id}, synchronize_session=False)
            db.commit()

        # Page count across every attachment, BEFORE any OCR runs - so the progress bar
        # can show "N of M" from its very first frame instead of only once the first page
        # happens to finish. Written to a throwaway scratch file and opened by PATH, the
        # exact same way the real OCR loop below opens each attachment (fitz.open(stream=...)
        # is a different call shape this codebase has never relied on elsewhere) - same
        # whole of every file, as that loop reads it too - no page limit.
        total_pages = 0
        if job is not None:
            count_dir = tmp / "_count"
            for cidx, (cname, cblob) in enumerate(files):
                cext = Path(cname).suffix.lower() or ".pdf"
                cpath = count_dir / f"{cidx}{cext}"
                try:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    cpath.write_bytes(cblob)
                    with fitz.open(cpath) as cdoc:
                        total_pages += len(cdoc)
                except Exception:  # noqa: BLE001
                    pass
            shutil.rmtree(count_dir, ignore_errors=True)
            if total_pages:
                email_progress.publish(job.id, 0, total_pages, pull_started_at)
        pages_done = 0

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
                        # Every page, however many - no page limit (see page_filter).
                        png = fdir / "pages" / f"page_{i}.png"
                        page.get_pixmap(dpi=150).save(png)
                        save_text_layer(page, png)   # OCR_ENGINE=pdftext only
                        if i == 1:
                            first_png = png
                        pages = i
                raw_pages = []
                # Each page's WORDS with their positions, kept beside its text - the flat
                # text cannot tell a column header from the value printed under it (see
                # doc_sets.invoice_number_from_tokens), the geometry can.
                raw_tokens: list[list[dict]] = []
                # Several pages at once when switched on (app/core/parallel_ocr.py); with
                # parallel_ocr_workers=1 this is the old page-by-page loop, call for call.
                from app.core.large_job_lane import in_lane
                from app.core.parallel_ocr import ocr_pages, workers_for

                file_pages_start = pages_done

                def _page_done(n_done: int, _start=file_pages_start) -> None:
                    if job is not None and total_pages:
                        email_progress.publish(job.id, _start + n_done, total_pages, pull_started_at)

                for res in ocr_pages([fdir / "pages" / f"page_{i}.png" for i in range(1, pages + 1)],
                                     workers=workers_for(in_lane()), label=name,
                                     on_page_done=_page_done, ocr=ocr_page_image):
                    raw_pages.append(res.get("text", ""))
                    raw_tokens.append(res.get("tokens") or [])
                pages_done += pages
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
                # Every kept page's own image, keyed by its KEPT-page number - used
                # only by the vision engine's per-page classifier
                # (classify_pages_from_images), mirroring smart_upload's own
                # page_images. Same reasoning: a wrong page in a combined email
                # attachment deserves the same fix as a manual upload.
                kept_to_orig = kept_page_to_original(pages, list(dropped))
                page_images = [
                    (i, fdir / "pages" / f"page_{kept_to_orig[i - 1] if i - 1 < len(kept_to_orig) else i}.png")
                    for i in range(1, len(kept) + 1)
                ]
            except Exception:  # noqa: BLE001
                logger.warning("could not open attachment %s", name)
                continue
            prepared.append({"name": name, "ext": ext, "blob": blob,
                             "zip_root": file_root[idx] if idx < len(file_root) else None,
                             "text": text, "image": first_png,
                             "drop_pages": list(dropped), "page_count": pages,
                             "page_images": page_images, "kept_pages": kept,
                             # Same pages as kept_pages, same order - see jobs.py's own copy.
                             "kept_tokens": [raw_tokens[o - 1] for o in kept_to_orig
                                             if o - 1 < len(raw_tokens)],
                             # How many of this file's pages were read, for billing - EVERY
                             # page, blank ones included (by request: a blank page costs the
                             # same render + OCR call as any other) - counted here, where raw_pages (before carrier-terms
                             # filtering) is still in scope, since a job may not exist yet at
                             # this point (a shared mailbox only creates one once the customer
                             # is identified, further down) to bill against directly.
                             "billable_pages": len(raw_pages)})
            doc_texts.append((name, text))

        if not prepared and not excel_files:
            _record(db, mid, sender, subject, "unreadable",
                    "the attachments could not be opened")
            _discard_pre_created_job(db, job)
            return {"from": sender, "subject": subject, "matched": None,
                    "reason": "attachments could not be read"}

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
            return {
                "from": sender, "subject": subject, "matched": None,
                "reason": "the documents do not identify any customer",
                "why": who.get("reason"), "evidence": who.get("evidence"),
                "left_unread": True,
            }

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
                return {
                    "from": sender, "subject": subject, "matched": None,
                    "reason": f"fits more than one customer ({names}) - needs a person",
                    "candidates": [by_id[k].name for k in keys],
                    "left_unread": True,
                }

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
            _discard_pre_created_job(db, job)
            return {
                "from": sender, "subject": subject, "matched": None,
                "reason": mode_reason, "left_unread": True,
            }

        # ---- the job
        # A single-template mailbox already created this (and its empty slots) immediately
        # after OCR started, long before classification got here - see this function's own
        # docstring. A shared mailbox could not: which customer this is for is exactly what
        # the block above just worked out, so only now does a job - and its own tenant,
        # which only exists once `group` is known - become possible at all.
        if job is None:
            # Assignment is a deliberate action from the Jobs list' own dropdown, never a
            # side effect of WHICH MAILBOX a job happened to arrive in - a forwarding agent
            # mails for six importers from one shared address, and none of them is "the"
            # operator for all six. The one exception: the SENDER is themselves a real,
            # already-registered operator account (their own email, not a shared inbox) -
            # there the job already has a genuine, unambiguous owner the moment it lands,
            # and leaving it Unassigned just made someone hunt for what they already sent.
            job = Job(
                tenant_id=group.tenant_id,
                group_id=group.id,
                reference=generate_job_no(),
                status="draft",
                created_by_id=None,
                assigned_operator_id=_operator_matching_sender(db, group.tenant_id, sender),
                # Straight off the mail that created it - see _eta_for_new_job.
                eta_date=_eta_for_new_job(msg, subject, group.tenant_id),
            )
            db.add(job)
            db.flush()
            # NOT group.documents: `group` came out of ctx (see _mailbox_setup), built on a
            # DIFFERENT, already-closed session - _pull_inbox's shared pool hands this
            # message to a worker with its own fresh session (see
            # _process_with_own_session), so the harvest session `group` was loaded in is
            # long gone by the time processing actually runs. Scalar columns already read
            # at load time (group.id, .name, .tenant_id, .mode) stay readable on a
            # detached object, but a RELATIONSHIP like .documents tries to run a fresh
            # query through that dead session and raises DetachedInstanceError - found
            # live: every message routed to a single-template mailbox crashed here, the
            # claim was never released, and the same email silently never became a job.
            # Querying through `db` (this call's own live session) instead of the
            # relationship sidesteps the whole problem. Same ordering group.documents
            # itself used (order_by order_index).
            for tdoc in (
                db.query(TemplateDocument)
                .filter(TemplateDocument.group_id == group.id)
                .order_by(TemplateDocument.order_index)
                .all()
            ):
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
        # OCR/document-capture is done either way at this point - clear the page progress
        # bar and move the badge on to Document Classifying for the (potentially slow)
        # per-attachment slot assignment just below, so the operator still sees SOMETHING
        # happening rather than the job appearing to freeze between "every page read" and
        # "documents actually sorted into slots".
        email_progress.clear(job.id)
        if attachment_notes:
            job.attachment_notes = attachment_notes
        job.stage_override = "Document Classifying"
        # Billed HERE, now that `job` definitely exists either way (the single-template fast
        # path created it before OCR even started; a shared mailbox only just created it,
        # above) - every attachment's own OCR pass already happened by this point regardless,
        # so this is the one moment both paths can bill against a real job. Same reasoning as
        # Smart Upload's own identical fix: the cost is the OCR pass itself, not whether a page
        # goes on to classify correctly or ends up in Unclassified, and a page with no real text
        # at all is not billed. The old call site that used to bill once a page became part of a
        # real document (the slot-write loop further down) no longer does, so a page is billed
        # exactly once per OCR pass, never twice.
        for item in prepared:
            record_billable_pages(db, job, item["billable_pages"])
        db.commit()
        cand_slots = list(slot_meta.values())
        # What has already gone into each slot, by content. Two attachments can carry
        # the SAME document - a supplier sends Invoice.pdf holding the invoice and the
        # packing list, then Packing List.pdf holding the same two - and the classifier
        # correctly reports both types in both files. Without this, each slot ends up
        # with two rows that are the same piece of paper (JOB-3DB0E9).
        placed_fingerprints: dict[str, set[str]] = {}

        # Stage every kept page as a provisional "unresolved" row BEFORE classification
        # runs - the exact same UnmatchedUploadPage mechanism Smart Upload already uses
        # (see that model's own docstring), just never wired into this pipeline until
        # now. A page the classifier cannot confidently place, or loses a same-slot
        # arbitration to another file's page (see the comment just below - "the real
        # packing list and freight certificate were silently dropped" was never fixed,
        # only made less frequent), must surface to an operator as Unclassified rather
        # than vanish with no trace. Pruned back down to just the pages that stay
        # genuinely unresolved once classification has actually run, below.
        import uuid

        unmatched_dir = Path(get_settings().uploads_dir) / "jobs" / job.id / "unmatched"
        unmatched_dir.mkdir(parents=True, exist_ok=True)
        pages_by_file: list[list] = []
        for item in prepared:
            rows: list = []
            page_image_by_num = dict(item.get("page_images") or [])
            for i, page_text in enumerate(item.get("kept_pages") or [], start=1):
                if not page_text.strip():
                    rows.append(None)
                    continue
                src_png = page_image_by_num.get(i)
                dest = unmatched_dir / f"{uuid.uuid4()}.png"
                if src_png is not None and src_png.exists():
                    shutil.copy(src_png, dest)
                row = UnmatchedUploadPage(
                    tenant_id=job.tenant_id, job_id=job.id,
                    original_filename=item["name"][:255], page_number=i,
                    image_path=str(dest), ocr_text=page_text,
                )
                db.add(row)
                rows.append(row)
            pages_by_file.append(rows)
        # A flush only, not a commit: if classification itself raises
        # AIServiceUnavailable below, these staged rows roll back with it. The job and its
        # empty slots are NOT part of that rollback anymore - they were already committed
        # above (so the operator's own browser could see Document Capture/Document
        # Classifying while this was still running) - the except block below explicitly
        # discards them instead (_discard_pre_created_job), so the net effect is the same:
        # nothing survives a deferred message, and the next poll starts completely fresh.
        db.flush()

        # assign_documents, not classify_file per file: a slot goes to ONE file, and
        # the most specific claimant wins it. Classifying each file alone let a single
        # invoice fill both the Invoice and the Packing List slot, and a bill of lading
        # fill both the BL and the Freight slot - the real packing list and freight
        # certificate were silently dropped.
        from app.core.extraction_queue import run_queued

        # Shares the same cap as extraction (see extraction_queue's own docstring) -
        # classification makes the identical kind of slow OpenAI call and was found holding
        # the same kind of resource open on 2026-11-03, just never through this queue before.
        claims_per_file = [] if not prepared else run_queued(lambda: classifier_module(group.mode).assign_documents_detailed(
            prepared, cand_slots, engine=extraction_engine,
            vision_model=vision_engine_model,
            # Classify every page from its own image instead of page 1 alone, same
            # fix as Smart Upload - a combined/multi-page attachment can mis-route a
            # page just as easily arriving by mail as uploaded by hand.
            per_page_vision=True,
            tenant_id=job.tenant_id,
        ))
        # ---- consolidation rule: a zip with a MAIN folder and sub-folders (an air consol -
        # master waybill in the main folder, one sub-folder per house shipment). The Air Waybill
        # is taken ONLY from the main folder; waybills in sub-folders go to Unclassified, grouped
        # as "Air Waybill". Only when the main folder really has a waybill - otherwise nothing
        # moves. If the main folder's waybill lost the classifier's cross-file tie-break (a file
        # claiming more kinds of document loses a contested slot), that file is classified
        # again on its own - one extra call, only for main-folder files left with no slot.
        awb_hold_pages: dict[int, set[int]] = {}
        consol_awb_keys: set[str] = set()
        awb_keys = {c["key"] for c in cand_slots if _is_waybill_slot(c)}
        if awb_keys and claims_per_file and any(it.get("zip_root") is True for it in prepared) \
                and any(it.get("zip_root") is False for it in prepared):
            def _has_awb(cl: list) -> bool:
                return any(c.get("key") in awb_keys and c.get("source") != "ai_second" for c in cl)

            sub_awb = [i for i, it in enumerate(prepared)
                       if it.get("zip_root") is False and _has_awb(claims_per_file[i])]
            root_idx = [i for i, it in enumerate(prepared) if it.get("zip_root") is True]
            if sub_awb and not any(_has_awb(claims_per_file[i]) for i in root_idx):
                for i in root_idx:
                    if any(c.get("source") != "ai_second" for c in claims_per_file[i]):
                        continue
                    try:
                        solo = run_queued(lambda it=prepared[i]: classifier_module(group.mode).assign_documents_detailed(
                            [it], cand_slots, engine=extraction_engine, vision_model=vision_engine_model,
                            per_page_vision=True, tenant_id=job.tenant_id))
                        claims_per_file[i] = (solo or [[]])[0]
                        logger.info("email classify: %s (main folder) classified on its own -> %s",
                                    prepared[i]["name"],
                                    ", ".join(slot_meta.get(c["key"], {}).get("name", "?") for c in claims_per_file[i]) or "nothing")
                    except AIServiceUnavailable:
                        raise
                    except Exception:  # noqa: BLE001 - then the rule simply does not apply
                        logger.exception("could not re-classify main-folder file %s", prepared[i]["name"])
            if sub_awb and any(_has_awb(claims_per_file[i]) for i in root_idx):
                consol_awb_keys = awb_keys
                for i in sub_awb:
                    moved = [c for c in claims_per_file[i] if c.get("key") in awb_keys]
                    claims_per_file[i] = [c for c in claims_per_file[i] if c.get("key") not in awb_keys]
                    awb_hold_pages[i] = {p for c in moved for p in (c.get("pages") or [])}
                logger.info("email classify: consolidation - Air Waybill from the main folder only; "
                            "%d sub-folder waybill(s) left in Unclassified", len(sub_awb))
        awb_label = next((slot_meta[k]["name"] for k in awb_keys if k in slot_meta), "Air Waybill")
        for _idx, (item, claims, unmatched_rows) in enumerate(zip(prepared, claims_per_file, pages_by_file)):
            # The AI second-opinion pathway (_ai_verify_second_type, source
            # "ai_second") is the weakest signal assign_documents_detailed produces -
            # a narrow, loosely-worded "does this ALSO contain Y" call with no regex
            # anchor, consulted only because nothing more reliable recognised the
            # wording. Found live: it accepted an ordinary invoice's own routine
            # freight cost line as "this is ALSO a Freight Certificate", and separately
            # accepted a plain single-page House BL as "this is ALSO a Packing List" -
            # two different document pairs, same underlying weak-signal cause. Rather
            # than keep hand-tuning prompts pair by pair, a weak claim never silently
            # fills a real slot at all - it is dropped, and (unless the SAME page also
            # has a real claim from a stronger source) the page stays staged as
            # Unclassified for a person to resolve by hand.
            strong_claims = [c for c in claims if c.get("source") != "ai_second"]
            weak_claims = [c for c in claims if c.get("source") == "ai_second"]
            for c in weak_claims:
                logger.info(
                    "email classify: %s - dropped a weak AI second-opinion claim on "
                    "%s (page(s) %s) - left for an operator to confirm instead of "
                    "silently duplicating into that slot",
                    item["name"], slot_meta.get(c["key"], {}).get("name", c["key"]),
                    c["pages"])
            # A combined multi-invoice file (several invoices back to back in one PDF)
            # looks like ONE claim to the classifier: every page equally looks like "an
            # Invoice page", so there is nothing for it to tell them apart with up front.
            # Split each claim wherever the invoice number printed on the pages actually
            # changes, BEFORE placement below - which already copes with a file producing
            # several claims for the same slot (it adds a row per claim, see file_index).
            #
            # THE SAME SPLIT SMART UPLOAD HAS HAD SINCE 2026-11-02 (20aafae). It was only
            # ever wired into _classify_and_place_files, and email has its own placement
            # code, so a customer whose supplier sends two invoices in one PDF got them as
            # ONE document by mail and two by hand. Found on JOB-1130B4 / JOB-CB9B46:
            # "[20261023] Commercial Invoice 380.PDF" is [inv 8667395, T&C, inv 8667396,
            # T&C] and arrived whole in the Invoice slot, read as a single invoice.
            split_claims: list[dict] = []
            for c in strong_claims:
                c_pages = c.get("pages") or []
                c_doc_type = slot_meta.get(c["key"], {}).get("doc_type")
                kept = item.get("kept_pages") or []
                page_texts = {
                    p: kept[p - 1] for p in c_pages if 1 <= p <= len(kept)
                }
                if (c_doc_type in doc_sets.GROUPABLE_DOC_TYPES
                        and len(c_pages) > 1 and len(page_texts) > 1):
                    kept_toks = item.get("kept_tokens") or []
                    page_tokens = {
                        p: kept_toks[p - 1] for p in c_pages if 1 <= p <= len(kept_toks)
                    }
                    groups = doc_sets.split_claim_pages_by_invoice_number(
                        c_doc_type, page_texts, page_tokens) or [c_pages]
                else:
                    groups = [c_pages]
                if len(groups) > 1:
                    logger.info(
                        "email classify: %s - %s holds %d separate invoices (%s), each "
                        "placed as its own document",
                        item["name"], slot_meta.get(c["key"], {}).get("name", c["key"]),
                        len(groups), groups)
                for g in groups:
                    split_claims.append({**c, "pages": g})
            strong_claims = split_claims

            keys = [c["key"] for c in strong_claims]
            # Every page a STRONG claim actually covers is now a real JobDocument - its
            # staging row has done its job (and would otherwise show the operator a
            # page that already lives somewhere real). A page only a weak claim covered
            # is deliberately NOT pruned - see above. Mirrors Smart Upload's own prune
            # step, extended with the same strong/weak distinction.
            claimed_pages = {p for c in strong_claims for p in c["pages"]}
            for i, row in enumerate(unmatched_rows, start=1):
                if row is not None and i in claimed_pages:
                    # The staging copy made for this page is now redundant too - see
                    # Smart Upload's own identical fix for why this must go with the row,
                    # not outlive it as an orphaned file nothing ever points to again.
                    if row.image_path:
                        Path(row.image_path).unlink(missing_ok=True)
                    db.delete(row)
                elif row is not None and i in awb_hold_pages.get(_idx, set()):
                    row.group_label = awb_label          # a sub-folder waybill (consolidation rule)
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
            #
            # ...and so must a file with ONE match that does not cover all of it. Filing it whole
            # put an air waybill and eight packing-list pages into the Invoice slot because only
            # the invoice on page 1 was recognised (JOB-FAE253). The pages no claim covers stay
            # Unclassified, where they were staged above, for an operator to place.
            split = item["ext"] == ".pdf" and (
                len(strong_claims) > 1
                or (len(strong_claims) == 1
                    and not claim_covers_file(strong_claims[0]["pages"], item.get("kept_pages") or []))
            )
            kept_original = (
                kept_page_to_original(item["page_count"], item["drop_pages"])
                if split else []
            )
            for claim in strong_claims:
                key = claim["key"]
                jd = slots.get(key)
                if jd is None:
                    continue
                # The same document arriving twice, in two different attachments. Skipped
                # rather than added as a second row: the operator would otherwise have to
                # work out which of two identical invoices to keep, on every such mail.
                #
                # Only an exact content match is skipped. Three genuinely different invoices
                # for one shipment read differently, so they fingerprint differently and all
                # three are still kept - which is the behaviour the row-adding below exists
                # for and must not lose. An empty fingerprint means "cannot tell" (too
                # little readable text) and never counts as a match.
                fingerprint = claim_fingerprint(item["text"] or "", claim.get("pages") or [])
                if not fingerprint:
                    # No text to compare (a scan, with OCR off): the same paper sent twice
                    # renders to the same pixels, so those stand in. A re-scan differs and is
                    # kept - "cannot tell" stays the safe answer. Production (Document AI on)
                    # never gets here with OCR text missing on purpose: unchanged.
                    fingerprint = _image_fingerprint(item.get("page_images") or [],
                                                     claim.get("pages") or [])
                already = placed_fingerprints.setdefault(key, set())
                if fingerprint and fingerprint in already:
                    logger.info(
                        "email classify: %s - skipped %s, the same document already came "
                        "in on another attachment in this mail",
                        item["name"], slot_meta.get(key, {}).get("name", key))
                    continue
                if fingerprint:
                    already.add(fingerprint)
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
                kept_txt = item.get("kept_pages") or []
                placed_pdf_texts.setdefault(key, []).append(
                    "\n".join(kept_txt[p - 1] for p in (claim.get("pages") or []) if 1 <= p <= len(kept_txt))
                    or (item["text"] or ""))
                # Not billed here - this attachment was already billed above, the moment it
                # was OCR'd, regardless of which slot (or none) it ends up in.
                if meta["name"] not in filled:
                    filled.append(meta["name"])
                named.append(f"{item['name']} -> {fname}")
        # Excel by sheet - after the PDFs, never weighed against them (see excel_intake).
        if excel_files:
            xl = intake_excel_files(db, job, group, excel_files, cand_slots, slots, placed_pdf_texts,
                                    blocked_keys=consol_awb_keys)
            for slot_name in xl.filled:
                if slot_name not in filled:
                    filled.append(slot_name)
            named.extend(xl.placed)
            if xl.skipped or xl.locked:
                notes = dict(job.attachment_notes or {})
                notes["skipped"] = list(notes.get("skipped") or []) + xl.skipped
                notes["locked"] = list(notes.get("locked") or []) + xl.locked
                job.attachment_notes = notes
        # Classification is done - clear the override so the job's own computed stage
        # (Document Capture once a slot has a real file, or whatever comes next) takes
        # over again. Left set, "Document Classifying" would win forever: stage_override
        # is a manual override by design, and nothing else ever clears one on its own.
        job.stage_override = None
        db.commit()
        # The pipeline's own working copy of this email is deleted in the finally
        # block below the moment this job is done processing - this is the only chance
        # to keep the as-arrived original for GK1's IRN Documents Upload "Prealert"
        # view.
        from app.core.job_email import save_original_email

        save_original_email(
            job.id, msg, files + excel_files, sender, subject,
            received_by=mail_email or get_settings().gmail_user,
        )
    except AIServiceUnavailable as exc:
        # The AI service itself failed (quota, rate limit, connection, 5xx) - this
        # message was never actually looked at, so it must not be recorded as "no
        # customer" or "no document matched". Undo the claim entirely: the very next
        # poll - an ordinary one, not only reexamine - tries it again from scratch,
        # once the service has recovered. Nothing was ever marked read (attachments
        # are read via BODY.PEEK[]), so there is no flag to undo either.
        db.rollback()
        # The job itself (and a single-template mailbox's own early creation, see this
        # function's own docstring) was already COMMITTED by this point - not just
        # flushed - so the operator's browser could actually see it while classification
        # ran. A plain rollback no longer undoes that on its own the way it used to
        # before this feature existed; explicitly discard it, or the retry this handler
        # is about to enable creates a genuine SECOND job once the AI service recovers.
        _discard_pre_created_job(db, job, unbill=True)
        if resume_job is not None:
            # A held job being read after its password was given: it simply goes back to
            # "Waiting for Password" (see _discard_pre_created_job) - the mail is not re-queued,
            # or the next poll would hold it a second time as a new job.
            _note_ai_down(str(exc))
            return {"from": sender, "subject": subject, "matched": None, "deferred": True,
                    "reason": "the AI service is temporarily unavailable - enter the password again later"}
        logger.warning(
            "email pull for %s deferred - AI service unavailable (%s); no mail is opened for "
            "the next %s minutes, then this one is retried", mid[:60], exc, AI_DOWN_WAIT_MINUTES)
        # Not _unclaim: that made the very next poll, a minute later, do all of this again.
        # Recorded as held up (and so visible to a Super Admin), and the wait started.
        _note_ai_down(str(exc))
        _record_failed(db, mid, sender, subject, "The AI service is unavailable: " + str(exc),
                       group_id=single_group.id if single_group is not None else None)
        return {
            "from": sender, "subject": subject, "matched": None,
            "reason": "the AI service is temporarily unavailable - retrying "
                      "automatically on a later poll",
            "deferred": True,
        }
    finally:
        # The temporary pages and OCR output go, whatever happened. Anything the job
        # needs has already been copied into the job's own folder.
        shutil.rmtree(tmp, ignore_errors=True)

    if group is None or job is None:
        return None

    # ---- no Air Waybill among the attachments? Build one from the pre-alert's own data.
    #
    # After classification on purpose: only now is it known whether a real waybill arrived.
    # Filed straight into the template's waybill slot (doc_type "BL"), so the extraction just
    # below reads it like any other document.
    if prealert_on:
        try:
            _file_prealert_awb(db, group.id, job, slots, _prealert_shipment, filled, named)
        except Exception:  # noqa: BLE001 - the job simply has no waybill, as before
            logger.exception("could not generate the Air Waybill for email job %s", job.id)
            db.rollback()

    if not filled:
        from app.models.job import UnclassifiedFile

        unclassified_count = (
            db.query(UnmatchedUploadPage)
            .filter(UnmatchedUploadPage.job_id == job.id)
            .count()
        ) + db.query(UnclassifiedFile).filter(UnclassifiedFile.job_id == job.id).count()
        if not unclassified_count and resume_job is None:
            # The customer was recognised, not one attachment belongs in any of their
            # slots, AND nothing survived as genuinely Unclassified either (every page
            # was blank/unreadable) - there is truly nothing to show an operator.
            # Keeping the job would put an empty shell in the queue, and marking the
            # mail read would make it unpullable - so the job goes and the mail stays.
            # Usually it means the template is missing a document type, and once that
            # is added the same mail can be pulled again.
            for jd in db.query(JobDocument).filter(JobDocument.job_id == job.id).all():
                db.delete(jd)
            db.delete(job)
            db.commit()
            _record(db, mid, sender, subject, "unrouted",
                    f"identified {group.name}, but none of the {len(files)} attachment(s) "
                    f"fits any of their document slots",
                    group_id=group.id, evidence=who.get("evidence") or "")
            return {
                "from": sender, "subject": subject, "matched": group.name,
                "reason": (f"identified {group.name}, but nothing attached fits their "
                           "document slots — no job created"),
                "left_unread": True,
            }
        # Nothing auto-filled a slot, but at least one page survived as genuinely
        # Unclassified - a real page the classifier could not confidently place, or
        # one that lost a same-slot arbitration to another file's page (the exact
        # "real packing list and freight certificate were silently dropped" case the
        # comment above assign_documents_detailed's own call describes). Keep the job
        # so an operator can resolve it by hand via the same Unclassified-pages tray
        # Smart Upload already has, instead of losing it with no trace.
        logger.info(
            "email classify: %s - nothing auto-filled, but %s page(s) kept as "
            "Unclassified for an operator to resolve", mid[:60], unclassified_count)

    try:
        limit_mb = getattr(group, "large_zip_mb", 5) or 5
        from app.core.large_job_lane import in_lane

        if in_lane():
            # Read on the large-job lane: its Run extraction / Re-extract go back to the lane
            # too (large_job_lane.is_lane_job). A Smart Upload never carries this mark.
            notes = dict(job.attachment_notes or {})
            notes["large_job"] = True
            job.attachment_notes = notes
            db.commit()
        if getattr(group, "large_zip_hold", False) is True and biggest_zip > limit_mb * 1024 * 1024:
            # A large zip (a big consolidation): read, classified and placed - extraction waits
            # for Run extraction so the documents can be checked first. Every other job of this
            # template still extracts automatically.
            notes = dict(job.attachment_notes or {})
            notes["large_zip"] = {"size_mb": round(biggest_zip / (1024 * 1024), 1), "limit_mb": limit_mb}
            job.attachment_notes = notes
            db.commit()
            logger.info("email %s: zip of %.1f MB is over %s MB - documents placed, extraction waits",
                        mid[:60], biggest_zip / (1024 * 1024), limit_mb)
        elif getattr(group, "auto_extract", True) is False:
            # This template reads, classifies and places only - extraction waits for someone to
            # press Extract (TemplateGroup.auto_extract). The job stays a draft with its slots filled.
            logger.info("email %s: auto-extract is off for %s - documents placed, extraction waits",
                        mid[:60], group.name)
        else:
            from app.core import extract_queue

            queued = False
            if extract_queue.enabled() and not in_lane():
                # EXTRACT_QUEUE on: this mail's work ends at placement; its extraction goes on
                # the extraction queue like every other one (app/core/extract_queue.py). Marked
                # "extracting" first - that is what the queue's worker checks before running.
                job.status = "extracting"
                job.extraction_is_rerun = False
                db.commit()
                try:
                    queued = extract_queue.enqueue(job.id) or True
                except Exception:  # noqa: BLE001 - never lose it: extract here instead
                    logger.exception("email %s: could not queue the extraction - extracting here",
                                     mid[:60])
            if not queued:
                run_extraction(db, job)
    except Exception:  # noqa: BLE001
        # Confirmed root cause of jobs sitting silently in "draft" forever with their pulled
        # email marked "matched" below as if nothing went wrong: logging and moving on left
        # NOTHING for an operator to ever notice - this job looked identical to one that
        # simply hadn't been picked up for extraction yet, with no event, no status change,
        # nothing in any dashboard bucket a person actually watches. run_extraction commits
        # as it goes (per-document flushes, see its own body) and may have died mid-write, so
        # this session's pending transaction must be rolled back before any further write.
        logger.exception("auto-extract failed for email job %s", job.id)
        db.rollback()
        job = db.get(Job, job.id)
        if job is not None and job.status not in (
            "extracted", "possible_duplicate", "processing", "completed",
        ):
            job.status = "failed"
            db.add(JobEvent(
                tenant_id=job.tenant_id, job_id=job.id, status="failed",
                stage="Data Validation",
                note="Automatic extraction failed right after this email was pulled in - "
                     "use Rerun extraction to try again.",
            ))
            db.commit()
    # Left UNREAD on purpose, even on success - that is how the inbox itself shows "this
    # one was turned into a job" to a person glancing at it. Processed mail is never
    # re-picked-up by mistake anyway, regardless of this flag: _claim() already recorded
    # it in EmailSeen the moment this run started, and the next poll's _claim() sees that
    # record and skips it outright - the mailbox's own read/unread flag plays no part in
    # that decision.
    #
    # A fresh, short-lived connection of its own for just this one STORE call, rather than
    # reusing the connection _harvest_mailbox used - that one may already be closed (it
    # always is, in this design: harvesting disconnects as soon as it has the raw bytes)
    # or, in principle, be busy on a different thread's next message entirely. One extra
    # IMAP login costs a fraction of a second against a message that just took minutes of
    # OCR and AI calls to process.
    try:
        if resume_job is not None:
            raise RuntimeError("resumed from a held job - no mailbox connection to update")
        flag_conn = _connect(mail_email, mail_app_password, mail_host)
        try:
            flag_conn.select("INBOX")
            flag_conn.store(uid, "-FLAGS", "\\Seen")
        finally:
            try:
                flag_conn.logout()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    _close_any_pending_email_for(db, mid, group.id, job.id)
    _record(db, mid, sender, subject, "matched",
            tie_note or (who.get("reason") or ""), group_id=group.id, job_id=job.id,
            evidence=who.get("evidence") or "")
    return {
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
    }


def _pull_one_mailbox(
    db: Session, tenant_id: str | None = None, max_messages: int = 30,
    reexamine: bool = False,
    mail_email: str | None = None, mail_app_password: str | None = None,
    mail_host: str | None = None, forced_group_id: str | None = None,
) -> dict:
    """Everything pull_inbox used to do end to end, for exactly ONE mailbox, run
    sequentially on the caller's own thread.

    Internally just a composition of _mailbox_setup -> _harvest_mailbox ->
    _process_one_message (once per harvested message) - the same three pieces _pull_inbox's
    own shared-pool version is built from, so there is exactly one copy of the actual
    per-message pipeline, not two that could silently drift apart.
    """
    from app.core.custom_page_filter import get_active_custom_filter_texts
    from app.core.system_settings import get_extraction_engine

    # Read ONCE, up front, before any tentative Job/JobDocument is ever flushed inside
    # _process_one_message. The first-ever read of a system setting lazily creates (and
    # commits) its row if none exists yet (see get_system_settings) - doing that lazily
    # INSIDE per-message processing, after a tentative Job was already flushed but not
    # committed, would incidentally commit that half-built job along with it, defeating the
    # rollback _process_one_message relies on when the classification call itself fails (see
    # test_classify_document_outage_rolls_back_*).
    extraction_engine = get_extraction_engine(db)
    vision_engine_model = get_settings().vision_engine_model
    custom_filter_texts = get_active_custom_filter_texts(db)

    ctx, early = _mailbox_setup(db, tenant_id, forced_group_id)
    if early is not None:
        return early

    harvest = _harvest_mailbox(mail_email, mail_app_password, mail_host, max_messages, reexamine)
    if not harvest.get("ok"):
        return {"ok": False, "error": harvest.get("error")}

    processed: list[dict] = []
    try:
        for message in harvest["messages"]:
            entry = _process_one_message(
                db, message["uid"], message["raw"], ctx,
                mail_email, mail_app_password, mail_host,
                extraction_engine, vision_engine_model, custom_filter_texts, reexamine,
            )
            if entry is not None:
                processed.append(entry)
                processed.extend(entry.get("split_into") or [])
        out = {"ok": True, "processed": processed}
        if ctx["unidentifiable"]:
            out["note"] = ("These customers have nothing on their documents that identifies "
                           "them, so mail can never be routed to them: "
                           + ", ".join(ctx["unidentifiable"]))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.exception("email pull failed")
        return {"ok": False, "error": str(exc), "processed": processed}

"""The ETA a pre-alert email carries, read off the mail itself at job creation.

Operations typed this in by hand on every Sea Import job, off an email that already says it.

Where it really appears, measured over the 42 pre-alert emails this product has kept:

    nowhere .................. 27   ("ETA: Not set" is the right answer for these)
    in the body .............. 12   ETA: 24-11-2026 / ETA: 06-11-2026
    in the body AND subject ... 3   FW: .../MB# OOLU8883030720/ETA: 06-Nov-2026

So the body is the main source and the subject is worth reading too - one real sender puts
it only in the subject line, run together with the HBL and MBL numbers by slashes.

Two passes, in this order:

1. ANCHORED PATTERNS. Find the word ETA (or "Estimated Time of Arrival") and take the date
   printed right after it, on that line. This is exact, free, instant, and - the thing that
   matters most here - it CANNOT pick up the ETD two lines above, because it never looks at
   a date that is not attached to an ETA label.

2. THE MODEL, only when the patterns find nothing. That is where the odd phrasings live: a
   date in a table cell, "arriving on the 19th", a wording nobody has seen yet. It is asked
   for one field and told in as many words not to answer with the ETD or the date the mail
   was sent.

Dates are read DAY-FIRST (19-11-2026 is 19 November), the same convention
extraction._DATE_FORMATS already uses - these are Indian customs documents, and a
month-first reading would silently turn the 11th of a month into November.

Returns "YYYY-MM-DD" or None. None is a perfectly good answer: most pre-alerts have no ETA,
and an invented one is far worse than a blank the operator fills in.
"""
import logging
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# "ETA", "E.T.A.", "Estimated Time of Arrival", "Arrival Date". Deliberately NOT "ETD",
# "Departure", or a bare "Arrival" (which shows up in "Arrival Notice", a document name).
_ANCHOR = re.compile(
    r"\b(?:ETA|E\.T\.A\.?|ESTIMATED\s+TIME\s+OF\s+ARRIVAL|ARRIVAL\s+DATE)\b",
    re.IGNORECASE,
)

# A date in any of the shapes these emails actually print.
_DATE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}"                                   # 2026-11-19
    r"|\d{1,2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{2,4}"            # 19-11-2026, 19/11/26
    r"|\d{1,2}\s*[-/.\s]\s*[A-Za-z]{3,9}\s*[-/.\s]\s*\d{2,4}"  # 19-Nov-2026, 19 Nov 2026
)

# Day first, always - see the module docstring.
_FORMATS = (
    "%d-%m-%Y", "%d-%m-%y",
    "%d/%m/%Y", "%d/%m/%y",
    "%d.%m.%Y", "%d.%m.%y",
    "%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y", "%d-%B-%y",
    "%d %b %Y", "%d %b %y", "%d %B %Y", "%d %B %y",
    "%d/%b/%Y", "%d.%b.%Y",
    "%Y-%m-%d",
)

# Worth asking the model about. An email that never mentions arriving at all has no ETA
# to find, and over the 42 real pre-alerts the patterns already caught 15 of the 15 that
# had one - so calling the model on every mail would be paying for a reliably empty
# answer on most of them. Deliberately loose ("arriv" catches arrival/arriving/arrives):
# the model is the net for wordings nobody has seen yet, and one extra call costs far
# less than a missed ETA.
_WORTH_ASKING = re.compile(r"\barriv|\bE\.?T\.?A\.?\b|\bberth|\bdischarg", re.IGNORECASE)

# How far past the label to look for its date. Wide enough for "Estimated Time of Arrival :
# 19 Nov 2026", short enough that it cannot wander into the next field on a crowded line.
_WINDOW = 48

# An ETA this far from today is a misread, not a sailing. Vessels are weeks away, not years:
# a two-digit year read wrong, or a reference number that parsed as a date, lands far
# outside this and is refused rather than written onto the job.
_PAST_DAYS = 365
_FUTURE_DAYS = 730


def _parse(text: str) -> date | None:
    cleaned = " ".join((text or "").replace(",", " ").split())
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    # "19 - Nov - 2026" and "19-Nov -2026" are the same date typed by different people.
    squashed = re.sub(r"\s*([-/.])\s*", r"\1", cleaned)
    if squashed != cleaned:
        for fmt in _FORMATS:
            try:
                return datetime.strptime(squashed, fmt).date()
            except ValueError:
                continue
    return None


def _sane(found: date, *, today: date | None = None) -> bool:
    today = today or date.today()
    delta = (found - today).days
    return -_PAST_DAYS <= delta <= _FUTURE_DAYS


def eta_from_patterns(text: str, *, today: date | None = None) -> str | None:
    """The date printed right after an ETA label. None when there is no such label, or
    nothing date-shaped beside it."""
    if not text:
        return None
    for m in _ANCHOR.finditer(text):
        # Only this line: the ETD is usually the line above or below, and crossing a newline
        # is exactly how a reader confuses the two.
        rest = text[m.end(): m.end() + _WINDOW]
        rest = rest.split("\n")[0]
        # ...and never past the NEXT label on the same line ("ETA: ... ETD: ...").
        nxt = re.search(r"\b(?:ETD|E\.T\.D\.?|MBL|HBL|MB#|HB#|VESSEL|VOYAGE)\b", rest,
                        re.IGNORECASE)
        if nxt:
            rest = rest[: nxt.start()]
        hit = _DATE.search(rest)
        if not hit:
            continue
        found = _parse(hit.group(0))
        if found and _sane(found, today=today):
            return found.isoformat()
        if found:
            logger.info("ETA %s read off the mail is too far from today - ignored",
                        found.isoformat())
    return None


def eta_from_model(text: str, *, tenant_id: str | None = None,
                   today: date | None = None) -> str | None:
    """Ask the model, for the wordings the patterns above do not know.

    Only ever reached when the patterns found nothing, so this costs one short call on the
    minority of mails, not on every one.
    """
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    settings = get_settings()
    body = (text or "").strip()
    if not settings.openai_api_key or not body:
        return None
    try:
        import json

        resp = create_chat_completion_with_retry(
            timeout=20,
            tenant_id=tenant_id,
            model=settings.openai_model,
            temperature=0,
            max_tokens=40,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are reading a shipping pre-alert email. Find the ETA - the "
                        "Estimated Time of ARRIVAL of the vessel at the destination port.\n\n"
                        "Rules, in order of importance:\n"
                        "1. NEVER return the ETD (Estimated Time of Departure), the sailing "
                        "date, or the date the email was sent or forwarded. If the only date "
                        "you can see is one of those, return an empty string.\n"
                        "2. Return a date only when the email ties it to arrival. If the "
                        "email does not give an arrival date at all, return an empty string. "
                        "An empty answer is correct and expected - most of these emails have "
                        "no ETA in them.\n"
                        "3. Day comes first in these documents: 06-11-2026 is 6 November "
                        "2026, never 11 June.\n\n"
                        'Reply with JSON: {"eta": "YYYY-MM-DD"} or {"eta": ""}.'
                    ),
                },
                {"role": "user", "content": body[:6000]},
            ],
        )
        raw = str(json.loads(resp.choices[0].message.content or "{}").get("eta", "")).strip()
        if not raw:
            return None
        try:
            found = date.fromisoformat(raw)
        except ValueError:
            found = _parse(raw)
        if found and _sane(found, today=today):
            return found.isoformat()
        logger.info("the model answered %r for the ETA - not a usable date, ignored", raw)
    except Exception as exc:  # noqa: BLE001 - never let this stop a job being created
        logger.warning("could not read the ETA with the model: %s", exc)
    return None


def extract_eta(subject: str, body: str, *, html: str | None = None,
                tenant_id: str | None = None, use_model: bool = True,
                today: date | None = None) -> str | None:
    """The job's ETA, as "YYYY-MM-DD", or None.

    Reads the BODY first (where it is in 15 of the 15 mails that have one) and then the
    SUBJECT (where 3 senders also put it, and one puts it run together with the HBL and MBL
    numbers by slashes). The HTML part is a fallback for a mail with no plain-text body.
    """
    body = body or ""
    subject = subject or ""
    for source in (body, subject, html or ""):
        got = eta_from_patterns(source, today=today)
        if got:
            return got
    if not use_model:
        return None
    # Subject first in the text handed to the model: it is short and specific, and a long
    # forwarded chain below it is mostly quoted headers.
    joined = f"Subject: {subject}\n\n{body or html or ''}".strip()
    if not _WORTH_ASKING.search(joined):
        # Nothing in this mail mentions arriving at all. There is no ETA here to find,
        # and asking anyway buys a reliably empty answer on the majority of pre-alerts.
        return None
    return eta_from_model(joined, tenant_id=tenant_id, today=today)

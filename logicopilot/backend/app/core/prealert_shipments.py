"""One pre-alert email, several shipments - and an Air Waybill built from the pre-alert itself.

An air pre-alert often covers more than one shipment in a single mail:

    Subject: FW: Pre-Alert: Juniper Networks Solution India Pvt //45T0523243 //4720797358// 4720797552
    Body:    a table, one row per shipment (FILE_NO, ETA, SHIPPER, HAWB, PCS, G_WGT, ...)
    Files:   22H0523041.pdf, 22H0523049.pdf, 22H0523050.pdf

Pulled as it stands that is ONE job holding three shipments' papers. This module is the checker
that runs in the email puller before any job exists (only on a template with
TemplateGroup.prealert_shipments switched on):

  1. named_hawbs          - the HAWB numbers the mail names: in the subject (after "//", or
                            listed after the word HAWB: "HAWB - 45T0524120, 45T0523984") and in
                            the body (a HOUSEBILL_NO / HAWB table column, "HAWB:" lines). One =
                            an ordinary single-shipment mail; more than one = try to split.
  2. read_shipments       - the MODEL reads the body and returns one record per shipment. A
                            model, not a parser: the table's columns, order and wording change
                            from sender to sender and mail to mail.
  3. assign_attachments   - which attachment belongs to which shipment: by the file/HAWB/MAWB
                            number in its name, then in its own text, then by asking the model.
                            A file nobody can place is LEFT OUT rather than guessed onto a
                            shipment it may not belong to.
  4. split_message        - one self-contained mail per shipment (its own subject, its own row
                            as the body, its own attachments). Never sent anywhere: the puller
                            processes each exactly as if it had arrived on its own.

And for any shipment whose mail carries no Air Waybill:

  5. build_awb_pdf        - a one-page "Air Waybill (Pre-Alert)" drawn from that shipment's
                            record, filed in the job's Air Waybill slot and read like any
                            other document. What the pre-alert does not say (dates, airline,
                            flight, shipper address) is simply absent, for an operator to add.

Nothing here raises into the puller: a mail this cannot make sense of is processed the way it
always was, as one job.
"""
from __future__ import annotations

import base64
import email.message
import email.utils
import json
import logging
import re
from email.message import EmailMessage

logger = logging.getLogger(__name__)

# Carried on every mail this module produces: the shipment's own record, so the puller does not
# have to ask the model for it a second time - and the mark that says "already split, never
# split again".
SHIPMENT_HEADER = "X-Cargora-Prealert-Shipment"

# A HAWB after "//": letters/digits (and inner hyphens), at least 6 long - "45T0523243",
# "4720797358", "176-23418102". Short tokens ("//FW", "//2") are not shipment numbers.
_SUBJECT_HAWB = re.compile(r"//\s*([A-Za-z0-9][A-Za-z0-9-]{5,})")

# The other way forwarders write it - "Pre-Alert | HAWB - 45T0524120, 45T0523984 | Juniper ..."
# (Expeditors, found live on JOB-90C47F: two shipments pulled as one job because the subject had
# no "//"). The word HAWB (also "HAWBs", "HAWB No.", "HAWB#"), then a list of numbers separated
# by commas, "/", "&" or "and". Never MAWB - \b before the H rules that out.
_SUBJECT_HAWB_LIST = re.compile(
    r"\bHAWBS?\b(?:\s*(?:NOS?|NUMBERS?)\b\.?)?\s*[:#.\-\u2013\u2014]*\s*(?P<list>[^|]*)", re.IGNORECASE)
_LIST_SEPARATOR = re.compile(r"\s*(?:,|;|&|/|\band\b)\s*|\s+", re.IGNORECASE)
_HAWB_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{5,}$")


def _listed_after_hawb(subject: str) -> list[tuple[str, tuple[int, int]]]:
    """(number, (start, end) of the whole "HAWB ... list" phrase) for each number listed after
    the word HAWB. The list ends at "|" or at the first word that is not a number (a name), so
    "HAWB 45T0524120 Juniper Networks 2026" reads one HAWB, not "2026"."""
    out: list[tuple[str, tuple[int, int]]] = []
    for m in _SUBJECT_HAWB_LIST.finditer(subject or ""):
        start, end = m.start(), m.start("list")
        for token in (t for t in _LIST_SEPARATOR.split(m.group("list")) if t):
            token = token.strip(".-")
            if not (_HAWB_TOKEN.match(token) and any(ch.isdigit() for ch in token)):
                break
            idx = subject.find(token, end)
            end = idx + len(token) if idx >= 0 else end
            out.append((token, (start, end)))
    return out

# Printed under the title of every waybill build_awb_pdf makes - and how one is recognised
# afterwards (is_generated_awb).
GENERATED_NOTE = "Generated from the pre-alert email"

FIELDS = ("hawb", "file_no", "mawb", "origin", "destination", "pieces", "gross_weight",
          "chargeable_weight", "shipper", "consignee", "eta")


def norm(value: str | None) -> str:
    """Letters and digits only, upper case - how two spellings of one number are compared."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def hawbs_from_subject(subject: str | None) -> list[str]:
    """Every distinct HAWB number the subject names - after "//" first, then any listed after
    the word HAWB - in order."""
    seen: set[str] = set()
    out: list[str] = []
    named = _SUBJECT_HAWB.findall(subject or "") + [n for n, _ in _listed_after_hawb(subject or "")]
    for raw in named:
        key = norm(raw)
        if key and key not in seen:
            seen.add(key)
            out.append(raw.strip("-"))
    return out


# ---------------------------------------------------------------- HAWBs in the BODY
#
# Some pre-alerts name their shipments only in the body - a table with a HOUSEBILL_NO column, or
# "HAWB: ..." lines - and say nothing (or only one number) in the subject. Read without the model:
# a table column whose heading is a house-bill heading, and "HAWB:" style lines. MASTERBILL / MAWB
# headings never count.
_HAWB_HEADING = re.compile(
    r"^\s*(?:H\.?\s*A\.?\s*W\.?\s*B\.?|HOUSE[\s_]*(?:AIR[\s_]*WAY[\s_]*)?BILL|HOUSE[\s_]*AWB|HBL)"
    r"(?:[\s_]*(?:NO|NOS|NUMBER|NUMBERS|#)\.?)?\s*$", re.IGNORECASE)
_BODY_HAWB_LINE = re.compile(
    r"\b(?:HAWB|HOUSE[\s_]*(?:AIR[\s_]*WAY[\s_]*)?BILL|HOUSEBILL|HOUSE[\s_]*AWB)"
    r"(?:[\s_]*(?:NO|NUMBER|#)\.?)?\s*[:#\-]\s*([A-Za-z0-9][A-Za-z0-9-]{5,})", re.IGNORECASE)


def _is_hawb_value(value: str) -> bool:
    v = " ".join(str(value or "").split()).strip(".-")
    return bool(_HAWB_TOKEN.match(v)) and any(ch.isdigit() for ch in v)


def _html_tables(html: str) -> list[list[list[str]]]:
    """Every <table> in the mail's HTML as rows of cell text."""
    from html.parser import HTMLParser

    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables, self.row, self.cell, self.depth = [], None, None, 0

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self.depth += 1
                self.tables.append([])
            elif tag == "tr" and self.depth:
                self.row = []
            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None and self.tables:
                if any(self.row):
                    self.tables[-1].append(self.row)
                self.row = None
            elif tag == "table" and self.depth:
                self.depth -= 1

        def handle_data(self, data):
            if self.cell is not None:
                self.cell.append(data)

    p = _P()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001 - a broken table just reads as nothing
        return []
    return [t for t in p.tables if t]


def _html_of(msg: email.message.Message | None) -> str:
    if msg is None:
        return ""
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            try:
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return ""
    return ""


def hawbs_from_body(body: str, html: str = "") -> list[str]:
    """HAWB numbers the body names: the values under a house-bill column in an HTML table, then
    "HAWB: <number>" style lines in the text. Distinct, in order."""
    found: list[str] = []
    for table in _html_tables(html):
        for r, row in enumerate(table):
            cols = [i for i, cell in enumerate(row) if _HAWB_HEADING.match(cell or "")]
            if not cols:
                continue
            for later in table[r + 1:]:
                for i in cols:
                    if i < len(later) and _is_hawb_value(later[i]):
                        found.append(" ".join(later[i].split()).strip(".-"))
            break
    found += [m.strip(".-") for m in _BODY_HAWB_LINE.findall(body or "")]
    seen: set[str] = set()
    out: list[str] = []
    for h in found:
        if norm(h) not in seen:
            seen.add(norm(h))
            out.append(h)
    return out


def read_shipments(subject: str, body: str, hawbs: list[str], *,
                   tenant_id: str | None = None, discover: bool = False,
                   file_names: list[str] | None = None) -> list[dict]:
    """One record per shipment in this pre-alert, read by the model. [] if it cannot be read.

    `hawbs` (from the subject) tells the model which shipments to look for; a record whose
    HAWB is not one of them is dropped, so a number it invented can never become a job. With no
    HAWB in the subject at all the model reports whatever single shipment the mail describes.
    """
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    settings = get_settings()
    text = (body or "").strip()
    if not settings.openai_api_key or not (text or (discover and subject)):
        return []
    if hawbs:
        wanted = ", ".join(hawbs)
    elif discover:
        # The model decides how many shipments there are - any subject or body format.
        wanted = ("(none given - report EVERY separate shipment this email covers. A shipment is one "
                  "House Air Waybill (HAWB) number; it may appear in the subject in any format (after "
                  "'//', after 'HAWB', comma or slash separated, in a sentence) or in the body (a table, "
                  "a list, a sentence). One HAWB with several invoices or packing lists is still ONE "
                  "shipment. If the email covers one shipment, return one record.)")
    else:
        wanted = "(none named in the subject - report the shipment the email describes)"
    names = ", ".join(file_names or [])
    try:
        resp = create_chat_completion_with_retry(
            timeout=60,
            tenant_id=tenant_id,
            model=settings.openai_model,
            temperature=0,
            max_tokens=1800,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are reading an AIR freight pre-alert email. It lists one or more "
                        "shipments, usually as a table with one row per shipment, but the "
                        "columns, their order and their names differ from email to email.\n\n"
                        "Return one record per shipment with these keys (use an empty string "
                        "for anything the email does not state - never guess, never copy a "
                        "value from another shipment's row):\n"
                        "  hawb               House Air Waybill number (HAWB / HOUSEBILL_NO / House)\n"
                        "  file_no            the forwarder's file / job number (FILE_NO)\n"
                        "  mawb               Master Air Waybill number (MAWB / MASTERBILL_NO)\n"
                        "  origin             origin airport or place, exactly as written (e.g. HAN)\n"
                        "  destination        destination airport or place, exactly as written (e.g. BLR)\n"
                        "  pieces             number of packages (PCS)\n"
                        "  gross_weight       gross weight, number only (G_WGT)\n"
                        "  chargeable_weight  chargeable weight, number only (CHG_WGT)\n"
                        "  shipper            shipper / supplier name\n"
                        "  consignee          consignee name\n"
                        "  eta                ETA exactly as written\n\n"
                        "Copy numbers character for character. When you are given a list of "
                        "HAWB numbers, only report shipments whose HAWB is in that list.\n"
                        'Reply with JSON: {"shipments": [ {...}, ... ]}.'
                    ),
                },
                {"role": "user",
                 "content": f"HAWB numbers named in the subject: {wanted}\n\nSubject: {subject}\n\n"
                            + (f"Attachments: {names}\n\n" if names else "") + text[:12000]},
            ],
        )
        rows = json.loads(resp.choices[0].message.content or "{}").get("shipments") or []
    except Exception as exc:  # noqa: BLE001 - the mail is then processed as it always was
        logger.warning("could not read the shipments off the pre-alert: %s", exc)
        return []

    wanted_keys = {norm(h): h for h in hawbs}
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        rec = {k: str(row.get(k) or "").strip() for k in FIELDS}
        key = norm(rec["hawb"])
        if not key or key in seen:
            continue
        if wanted_keys:
            if key not in wanted_keys:
                continue
            rec["hawb"] = wanted_keys[key]      # the subject's own spelling
        seen.add(key)
        out.append(rec)
    if wanted_keys:
        out.sort(key=lambda r: list(wanted_keys).index(norm(r["hawb"])))
    return out


def _first_text(blob: bytes, name: str, limit: int = 4000) -> str:
    """The embedded text of a PDF's first pages - free and instant; "" for a scan or an image."""
    if not name.lower().endswith(".pdf"):
        return ""
    try:
        import fitz

        with fitz.open(stream=blob, filetype="pdf") as doc:
            return " ".join(doc[i].get_text() for i in range(min(2, doc.page_count)))[:limit]
    except Exception:  # noqa: BLE001
        return ""


def assign_attachments(shipments: list[dict], files: list[tuple[str, bytes]], *,
                       tenant_id: str | None = None) -> tuple[dict[str, list[int]], list[int]]:
    """({hawb: [index into files]}, [indexes nobody could place]).

    Cheapest evidence first: the shipment's file / HAWB / MAWB number in the attachment's NAME,
    then in its own text. Only what is still unplaced goes to the model, with the names and a
    little text of each; and what the model cannot place either is left out.
    """
    keys = {s["hawb"]: [k for k in (norm(s.get("file_no")), norm(s["hawb"]), norm(s.get("mawb"))) if len(k) >= 6]
            for s in shipments}
    placed: dict[str, list[int]] = {s["hawb"]: [] for s in shipments}
    texts = [_first_text(blob, name) for name, blob in files]
    loose: list[int] = []
    for i, (name, _blob) in enumerate(files):
        hits = [h for h, ks in keys.items() if any(k in norm(name) for k in ks)]
        if len(hits) != 1:
            hits = [h for h, ks in keys.items() if any(k in norm(texts[i]) for k in ks)]
        if len(hits) == 1:
            placed[hits[0]].append(i)
        else:
            loose.append(i)
    if loose:
        for i, hawb in _assign_with_model(shipments, files, texts, loose, tenant_id).items():
            placed[hawb].append(i)
            loose.remove(i)
    for hawb in placed:
        placed[hawb].sort()
    return placed, loose


def _assign_with_model(shipments, files, texts, loose, tenant_id) -> dict[int, str]:
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    settings = get_settings()
    if not settings.openai_api_key:
        return {}
    listing = "\n".join(
        f"[{i}] name: {files[i][0]}\n    text: {(texts[i] or '(no readable text - a scan or an image)')[:700]}"
        for i in loose)
    try:
        resp = create_chat_completion_with_retry(
            timeout=60, tenant_id=tenant_id, model=settings.openai_model, temperature=0,
            max_tokens=400, response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "An air pre-alert email covers several shipments. Decide which shipment "
                    "each attachment belongs to, from its file name and text - matching a "
                    "HAWB, MAWB or file number, a shipper name, piece count or weight.\n"
                    "Answer ONLY when the evidence is clear. If an attachment could belong to "
                    "more than one shipment, or to none, leave it out of your answer.\n"
                    'Reply with JSON: {"matches": [{"attachment": <number>, "hawb": "<hawb>"}]}.')},
                {"role": "user", "content": "Shipments:\n" + json.dumps(shipments, ensure_ascii=False)
                                            + "\n\nAttachments:\n" + listing},
            ],
        )
        matches = json.loads(resp.choices[0].message.content or "{}").get("matches") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not match the pre-alert's attachments with the model: %s", exc)
        return {}
    by_key = {norm(s["hawb"]): s["hawb"] for s in shipments}
    out: dict[int, str] = {}
    for m in matches if isinstance(matches, list) else []:
        try:
            i = int(m.get("attachment"))
        except (TypeError, ValueError, AttributeError):
            continue
        hawb = by_key.get(norm(m.get("hawb")))
        if i in loose and hawb and i not in out:
            out[i] = hawb
    return out


# What the generated waybill prints, in order: (label, record key). The labels are the ones the
# Air Waybill's own fields are read by - HAWB / MAWB number, airport of departure and
# destination, pieces, gross weight, shipper - so the ordinary extraction finds them.
_AWB_LINES = (
    ("HAWB Number (House Air Waybill No.)", "hawb"),
    ("MAWB Number (Master Air Waybill No.)", "mawb"),
    ("File No.", "file_no"),
    ("Airport of Departure (Origin)", "origin"),
    ("Airport of Destination (Port of Entry)", "destination"),
    ("Mode of Transport", None),
    ("No. of Packages (Pieces)", "pieces"),
    ("Gross Weight", "gross_weight"),
    ("Chargeable Weight", "chargeable_weight"),
    ("Shipper (Supplier) Name", "shipper"),
    ("Consignee Name", "consignee"),
    # The ETA is deliberately NOT printed. It is the only date the pre-alert has, and on the
    # first real mail (JOB-646C52) the waybill's MAWB Date field took it as the master
    # waybill's date. The job already gets its ETA from the mail itself (_eta_for_new_job).
)


def build_awb_pdf(shipment: dict) -> bytes:
    """A one-page "Air Waybill (Pre-Alert)" for one shipment, from its pre-alert record.

    Plain label-and-value lines in real (selectable) text, so it reads cleanly whether it is
    OCR'd or not. A value the pre-alert did not give is left off the page entirely - an empty
    box on the job says "not known" more honestly than a printed blank that might be misread.
    """
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)          # A4
    page.insert_text((50, 70), "AIR WAYBILL (PRE-ALERT)", fontsize=18, fontname="hebo")
    page.insert_text((50, 92), f"{GENERATED_NOTE} - not the carrier's own waybill.",
                     fontsize=9, fontname="helv", color=(0.35, 0.35, 0.35))
    page.draw_line((50, 102), (545, 102), color=(0.6, 0.6, 0.6), width=0.7)
    y = 130
    for label, key in _AWB_LINES:
        value = "AIR" if key is None else str(shipment.get(key) or "").strip()
        if not value:
            continue
        if key == "gross_weight" or key == "chargeable_weight":
            value = f"{value} KGS" if re.fullmatch(r"[\d.,]+", value) else value
        page.insert_text((50, y), f"{label}:", fontsize=11, fontname="hebo")
        page.insert_textbox(fitz.Rect(300, y - 11, 545, y + 30), value, fontsize=11, fontname="helv")
        y += 30
    out = doc.tobytes()
    doc.close()
    return out


def is_generated_awb(path) -> bool:
    """Is this file a waybill THIS module generated (build_awb_pdf), rather than one a carrier
    issued? Read off the page's own text - the note under the title - so it holds for as long as
    the file exists, through every re-extraction, with nothing extra to store."""
    try:
        if not str(path).lower().endswith(".pdf"):
            return False
        import fitz

        with fitz.open(str(path)) as doc:
            return doc.page_count > 0 and GENERATED_NOTE in doc[0].get_text()
    except Exception:  # noqa: BLE001
        return False


def awb_filename(shipment: dict) -> str:
    return f"AWB_{norm(shipment.get('hawb')) or 'PREALERT'}.pdf"


def shipment_from_message(msg: email.message.Message) -> dict | None:
    """The shipment record a split mail carries (see SHIPMENT_HEADER), or None."""
    raw = msg.get(SHIPMENT_HEADER)
    if not raw:
        return None
    try:
        # Written out, a header this long is wrapped in RFC 2047 encoded-words and folded
        # across lines - undo both before decoding the record itself.
        from email.header import decode_header, make_header

        text = str(make_header(decode_header(str(raw))))
        rec = json.loads(base64.b64decode("".join(text.split())).decode("utf-8"))
        return rec if isinstance(rec, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _body_for(shipment: dict) -> str:
    lines = ["Pre-alert for one shipment (split from a pre-alert covering several).", ""]
    labels = {"hawb": "HAWB", "file_no": "FILE NO", "mawb": "MAWB", "origin": "ORIGIN",
              "destination": "DESTINATION", "pieces": "PCS", "gross_weight": "G_WGT",
              "chargeable_weight": "CHG_WGT", "shipper": "SHIPPER", "consignee": "CONSIGNEE",
              "eta": "ETA"}
    for key in FIELDS:
        if shipment.get(key):
            lines.append(f"{labels[key]}: {shipment[key]}")
    return "\n".join(lines) + "\n"


def _subject_without_hawbs(subject: str | None) -> str:
    """The subject with every HAWB it names taken out - the "//..." tail and any "HAWB - a, b"
    list - so each split mail can name just its own HAWB the usual way ("<rest> //<hawb>")."""
    text = (subject or "").split("//", 1)[0]
    # One span per "HAWB ..." phrase - each listed number repeats the phrase start with a growing
    # end, so keep the furthest end, then cut from the back so earlier offsets stay valid.
    phrase_end: dict[int, int] = {}
    for _, (start, end) in _listed_after_hawb(text):
        phrase_end[start] = max(end, phrase_end.get(start, end))
    for start, end in sorted(phrase_end.items(), reverse=True):
        text = text[:start] + text[end:]
    text = re.sub(r"(\s*\|\s*)+", " | ", text)          # "a |  | b" -> "a | b"
    return " ".join(text.split()).strip(" |-:").strip()


def split_message(msg: email.message.Message, subject: str, shipments: list[dict],
                  files: list[tuple[str, bytes]], placed: dict[str, list[int]]) -> list[bytes]:
    """One raw mail per shipment: the original's sender and recipients, a subject naming just
    this HAWB, this shipment's own row as the body, and only its own attachments."""
    prefix = _subject_without_hawbs(subject) or "Pre-Alert"
    parent_id = (msg.get("Message-ID") or "").strip().strip("<>") or "prealert"
    out: list[bytes] = []
    for shipment in shipments:
        sub = EmailMessage()
        for header in ("From", "To", "Cc", "Date"):
            if msg.get(header):
                sub[header] = str(msg.get(header))
        sub["Subject"] = f"{prefix} //{shipment['hawb']}"
        sub["Message-ID"] = f"<{parent_id}.{norm(shipment['hawb'])}@cargora-split>"
        sub[SHIPMENT_HEADER] = base64.b64encode(
            json.dumps(shipment, ensure_ascii=False).encode("utf-8")).decode("ascii")
        sub.set_content(_body_for(shipment))
        for i in placed.get(shipment["hawb"], []):
            name, blob = files[i]
            is_pdf = name.lower().endswith(".pdf")
            sub.add_attachment(blob, maintype="application" if is_pdf else "application",
                               subtype="pdf" if is_pdf else "octet-stream", filename=name)
        out.append(sub.as_bytes())
    return out


def named_hawbs(msg: email.message.Message | None, subject: str, body: str) -> tuple[list[str], list[str]]:
    """(every HAWB the mail names - subject first, then body; the ones named ONLY in the body)."""
    from_subject = hawbs_from_subject(subject)
    seen = {norm(h) for h in from_subject}
    body_only = [h for h in hawbs_from_body(body, _html_of(msg)) if norm(h) not in seen]
    return from_subject + body_only, body_only


def may_cover_several(msg: email.message.Message, subject: str, body: str) -> bool:
    """Cheap check (no model) - is this mail worth asking whether it covers several shipments?"""
    if msg.get(SHIPMENT_HEADER):
        return False
    return len(named_hawbs(msg, subject, body)[0]) >= 2


def plan_split(msg: email.message.Message, subject: str, body: str,
               files: list[tuple[str, bytes]], *, tenant_id: str | None = None,
               found: dict | None = None) -> list[bytes] | None:
    """The per-shipment mails for a pre-alert covering several shipments, or None to process the
    mail as it stands (one shipment, already split, or not readable as several).

    The shipments are the HAWBs the subject names plus any the body names (a HOUSEBILL_NO column,
    "HAWB:" lines). Only splits when the model found a record for at least two of them - a mail
    that merely looks like several numbers must never turn one shipment into three empty jobs.
    A shipment named ONLY in the body must also have an attachment of its own: a forwarded mail
    can quote an older pre-alert's table, and those shipments are not this mail's to create.

    When the rules find fewer than two, THE MODEL decides - so a subject or body in any format
    (now or in future) is understood, not only the ones written as rules here. Its answer is
    only trusted when every HAWB it reports is printed in the mail itself (subject, body or an
    attachment's name) and, as above, has an attachment of its own. A mail with fewer than two
    attachments can never pass that, so the model is not asked about it at all. `found`, when
    given, receives the single shipment the model read ("single") so the caller need not ask
    the same question again to build the Air Waybill.
    """
    if msg.get(SHIPMENT_HEADER):
        return None
    hawbs, body_only = named_hawbs(msg, subject, body)
    if len(hawbs) >= 2:
        shipments = read_shipments(subject, body, hawbs, tenant_id=tenant_id)
    elif len(files) >= 2:
        names = [n for n, _ in files]
        recs = read_shipments(subject, body, [], tenant_id=tenant_id, discover=True, file_names=names)
        printed = norm(" ".join([subject or "", body or "", _html_of(msg), *names]))
        shipments = [r for r in recs if norm(r["hawb"]) and norm(r["hawb"]) in printed]
        invented = [r["hawb"] for r in recs if r not in shipments]
        if invented:
            logger.info("pre-alert %r: model reported HAWB(s) %s not printed in the mail - ignored",
                        (subject or "")[:80], invented)
        if found is not None and len(shipments) == 1:
            found["single"] = shipments[0]
        named = {norm(h) for h in hawbs}
        body_only = body_only + [r["hawb"] for r in shipments if norm(r["hawb"]) not in named]
        if len(shipments) < 2:
            return None
        logger.info("pre-alert %r: model found %d shipments %s", (subject or "")[:80],
                    len(shipments), [r["hawb"] for r in shipments])
    else:
        return None
    if len(shipments) < 2:
        logger.info("pre-alert %r names %d HAWBs but %d could be read from the body - not split",
                    (subject or "")[:80], len(hawbs), len(shipments))
        return None
    placed, loose = assign_attachments(shipments, files, tenant_id=tenant_id)
    if body_only:
        only = {norm(h) for h in body_only}
        dropped = [s["hawb"] for s in shipments if norm(s["hawb"]) in only and not placed.get(s["hawb"])]
        if dropped:
            shipments = [s for s in shipments if s["hawb"] not in dropped]
            logger.info("pre-alert %r: %s named only in the body with no attachment of its own - left out",
                        (subject or "")[:80], dropped)
        if len(shipments) < 2:
            logger.info("pre-alert %r: fewer than two shipments with their own attachments - not split",
                        (subject or "")[:80])
            return None
        loose = [i for i in range(len(files)) if not any(i in placed.get(s["hawb"], []) for s in shipments)]
    logger.info("pre-alert %r split into %d shipments: %s; attachments left out: %s",
                (subject or "")[:80], len(shipments),
                {s["hawb"]: [files[i][0] for i in placed.get(s["hawb"], [])] for s in shipments},
                [files[i][0] for i in loose])
    return split_message(msg, subject, shipments, files, placed)

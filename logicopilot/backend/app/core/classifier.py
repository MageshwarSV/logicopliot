"""Document classifier — decides which document type an uploaded file is, so a
manual upload or an email auto-pull can route each file to the right slot.

OCR is done by Document AI (with a vision fallback); the classification decision
is made by OpenAI from the content (BL / Packing List / Invoice / Freight / Other).
A single file may be a COMBINED document (e.g. BL+PL) and match multiple types.
"""

import base64
import json
import logging
import re
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class AIServiceUnavailable(Exception):
    """The AI call itself could not be completed - quota exhausted, rate limited, a dropped
    connection, a timeout, or a 5xx from the provider - as distinct from a call that DID
    complete and legitimately found nothing.

    A caller building a job from a batch of documents must not treat this the same as "no
    match": recording a permanent negative verdict (no customer identified, no document type
    recognised) here would be wrong twice over - it blocks the message from ever being tried
    again automatically, for a reason that has nothing to do with the message itself and will
    very likely have cleared up by the next poll.
    """

# Content hints per doc type, to steer the classifier.
DOC_TYPE_HINTS = {
    "BL": "bill of lading, sea/air waybill, B/L no, shipper, consignee, notify party, vessel, port of loading/discharge",
    "PackingList": "packing list, number of packages/cartons, net weight, gross weight, dimensions, marks & numbers",
    "Invoice": "commercial invoice, invoice no, invoice date, unit price, amount, total value, incoterm, HS code",
    "Freight": "freight certificate/invoice, freight charges, ocean freight, THC, BAF, freight amount",
}

# Words a customer uses when NAMING a slot, mapped to the built-in hint for that kind of
# document. A slot typed "Custom" gets no hint at all otherwise - which is how a slot called
# "Fright Certificate" (spelt as the customer spells it) ended up described only by its field
# list and never matched a real freight certificate. Spelling variants are deliberate: these are
# names people typed, not a controlled vocabulary.
_NAME_HINTS = (
    (("freight", "fright", "frieght", "frt"), "Freight"),
    (("packing", "package", "pacakage", "packging"), "PackingList"),
    (("lading", "waybill", "b/l", "bl no", "bill of l"), "BL"),
    (("invoice", "inv "), "Invoice"),
)


def _key_from_name(name: str) -> str | None:
    """Which of the 4 built-in types this slot's own NAME reads as, or None. Shared by
    _hint_from_name (the description shown to the model) and _effective_doc_type (which
    signature table entry a "Custom"-typed slot should still be checked against) - the same
    slot called "Fright Certificate" must mean the same thing to both, or a slot could get a
    Freight hint in its description while never actually being checked against the Freight
    keyword backstop, which is exactly the gap that let a real arrival notice go unrescued."""
    low = (name or "").lower()
    for words, key in _NAME_HINTS:
        if any(w in low for w in words):
            return key
    return None


def _hint_from_name(name: str) -> str:
    """The built-in hint for whatever the customer called this slot, or ''."""
    key = _key_from_name(name)
    return DOC_TYPE_HINTS.get(key, "") if key else ""


def _describe_examples(candidate: dict) -> str:
    """Real documents an operator has manually confirmed belong to this slot (see
    ClassificationExample in app/models/job.py) - this module stays free of any database
    access, so the caller (jobs.py/email_puller.py) passes these in on the candidate dict
    itself, under "examples": [{"keywords": [...], "snippet": "..."}, ...].

    Worded the same way as THE CUSTOMER'S OWN SAMPLE above, because it is the same kind of
    evidence - a real document of this type, just confirmed by a correction afterwards
    instead of at template setup. This is what lets a document worded nothing like "freight
    certificate" (an Arrival Notice, say) get recognised on its own after the first one is
    ever manually corrected, without needing its exact wording hand-coded anywhere.
    """
    examples = [e for e in (candidate.get("examples") or []) if isinstance(e, dict)]
    if not examples:
        return ""
    lines = []
    for e in examples[:3]:
        kw = ", ".join(str(k) for k in (e.get("keywords") or [])[:10])
        snippet = str(e.get("snippet") or "")[:600]
        bits = []
        if kw:
            bits.append(f"keywords: {kw}")
        if snippet:
            bits.append(f'excerpt: "{snippet}"')
        if bits:
            lines.append("; ".join(bits))
    if not lines:
        return ""
    return ("CONFIRMED BY THIS TENANT'S OWN OPERATOR - real documents matched here before "
            "(treat these the same as the customer's own sample above): " + " | ".join(lines))


def _effective_doc_type(candidate: dict) -> str | None:
    """Which _TYPE_SIGNATURES entry this slot should be checked against for the keyword
    backstop - its own structured doc_type when that is already one of the 4 built-in types,
    otherwise whatever its NAME reads as (a "Custom"-typed slot named "Fright Certificate" is
    still, for this purpose, a Freight slot - see _key_from_name). A slot whose type is
    genuinely bespoke and whose name matches nothing returns None, same as before: it is
    simply never checked, exactly like "BL" (no signature exists) already is not."""
    doc_type = candidate.get("doc_type")
    if doc_type in _TYPE_SIGNATURES:
        return doc_type
    return _key_from_name(candidate.get("name") or "")


# ---------------------------------------------------------------------------------------------
# A deterministic backstop for a genuinely COMBINED document - one page/file that is truly
# BOTH an Invoice and a Packing List at once ("SHIPPING INVOICE CUM PACKING LIST" is exactly
# this, printed as its own title). The model's single classification call sometimes notices
# only one of the two even when both are plainly there; this checks the page's own text for
# strong, specific signatures of whichever type it did NOT already find, and adds it - never
# invents a type from nothing, and never fires on an ordinary single-type document that
# merely mentions one incidental word (a REQUIRED phrase plus several independent SUPPORTING
# markers must all be present, the same two-tier principle page_filter's carrier-terms
# detector already uses).
# ---------------------------------------------------------------------------------------------
_TYPE_SIGNATURES: dict[str, dict] = {
    "PackingList": {
        # A real combined "Delivery Challan cum Commercial Invoice / Packing Slip" calls
        # itself a "Packing Slip" and labels its table "PACKING DETAILS" - never the words
        # "packing list" anywhere on the page.
        "required": [r"packing\s*list", r"packing\s*slip", r"packing\s*detail"],
        "supporting": [
            r"net\s*weight", r"gross\s*weight", r"no\.?\s*(of|&)?\s*(kind\s*of\s*)?pack",
            r"dimension", r"marks?\s*(&|and)\s*nos?", r"total\s*no\.?\s*of\s*packs?",
        ],
        "min_supporting": 2,
    },
    "Invoice": {
        # Same document: its own field label reads "Commercial Inv No / Date", and its title
        # reads "Commercial Invoice / Packing Slip" - "invoice no" alone matches neither.
        "required": [r"invoice\s*no", r"inv\s*no", r"commercial\s*invoice", r"tax\s*invoice"],
        "supporting": [
            r"invoice\s*date", r"rate\s*in\s*usd", r"unit\s*price", r"amount\s*in\s*usd",
            r"total\s*(invoice\s*)?value", r"\bhsn\b", r"terms\s*of\s*payment",
        ],
        "min_supporting": 2,
    },
    "Freight": {
        # A real freight certificate/invoice states an actual freight CHARGE line, not just
        # the word "freight" in passing (a bill of lading's own "Freight Payable at
        # Destination" box, say, is not this). A forwarder's own arrival notice very often
        # carries this same charge line under its own heading, with no page ever printed
        # "freight certificate" - so a specific dollar-figure charge line is the required
        # signal, not the document's own title.
        "required": [
            r"ocean\s*freight", r"freight\s*charges?", r"freight\s*amount",
            r"origin\s*charges", r"destination\s*charges", r"freight\s*certificate",
            r"freight\s*invoice",
        ],
        "supporting": [
            r"\bthc\b", r"\bbaf\b", r"handling\s*charges", r"chargeable\s*weight",
            r"(usd|inr|eur|gbp)\s*[\d,]+\.\d{2}", r"tracking\s*no",
        ],
        "min_supporting": 1,
    },
}


def _keyword_signature_match(text: str, doc_type: str) -> tuple[bool, int]:
    """(is_match, how many supporting markers hit) - is_match requires the type's REQUIRED
    phrase plus at least min_supporting of its supporting markers, both independently."""
    sig = _TYPE_SIGNATURES.get(doc_type)
    if not sig or not text:
        return False, 0
    if not any(re.search(p, text, re.IGNORECASE) for p in sig["required"]):
        return False, 0
    hits = sum(1 for p in sig["supporting"] if re.search(p, text, re.IGNORECASE))
    return hits >= sig["min_supporting"], hits


def _ai_verify_second_type(filename: str, text: str, image_path: Path | None,
                           candidate: dict, already_types: list[str]) -> dict | None:
    """The regex backstop's fallback for wording it does not recognise: ask the model itself,
    narrowly, whether this ONE remaining type is ALSO present - separate from the regex word
    list, so a customer whose document never uses the words "invoice" or "packing list" at all
    (a despatch note, a delivery challan, whatever their forwarder calls it) still gets caught.
    Only called when the file already matched something and this specific type is still
    unclaimed, so it costs nothing for the vast majority of ordinary, single-type documents.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    hint = DOC_TYPE_HINTS.get(candidate["doc_type"]) or _hint_from_name(candidate.get("name") or "")
    already = ", ".join(already_types) or "another document type"
    instruction = (
        f"This file was already found to contain a {already}. Now check independently: does "
        f"the SAME file ALSO, separately, contain a {candidate['name']} ({candidate['doc_type']})? "
        "This happens when one page or file legitimately combines two document types at once - "
        "for example a delivery challan that is also the commercial invoice and packing list "
        "combined. The exact words \"invoice\" or \"packing list\" may never appear at all, so "
        "judge by CONTENT, never by exact terminology: an invoice has prices and a total "
        "payable; a packing list has a per-package weight/dimension breakdown.\n\n"
        f"What a {candidate['doc_type']} typically contains: {hint or 'no hint available'}\n\n"
        f"Filename: {filename}\n\n"
        'Return JSON: {"present": true/false, "pages": [1], '
        '"evidence": "the words on the page that make it this type"}. '
        "A claim you cannot back with an exact quote must be false."
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        if text and text.strip():
            content: object = instruction + "\n\n--- DOCUMENT TEXT ---\n" + text[:9000]
        elif image_path and image_path.exists():
            b64 = base64.b64encode(image_path.read_bytes()).decode()
            content = [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
        else:
            return None
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=300,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        if not data.get("present"):
            return None
        ev = str(data.get("evidence") or "").strip()
        if len(ev) < 8:
            return None
        pages = [int(p) for p in (data.get("pages") or []) if str(p).isdigit()]
        return {"pages": pages or None, "evidence": ev[:300]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("second-type AI verification failed for %s (%s): %s",
                       filename, candidate["doc_type"], exc)
        return None


def _augment_combined_document_claims(files: list[dict], candidates: list[dict],
                                      claims: list[list[dict]]) -> None:
    """Mutates `claims` in place: for each file, checks whether its own text carries a
    strong, specific signature of a type this template has a slot for, that the model's own
    call did not already claim for it. Runs for a file with at least one existing claim (a
    combined document's SECOND type - covers the same pages already matched, since a combined
    document's second type lives on the same page, not somewhere else in the file) AND for a
    file with NO claims at all: a single classification call can simply miss a type that is
    plainly there (a forwarder's arrival notice stating real freight charges, matched to
    nothing, was exactly this) - regex evidence does not need the model to have gotten
    anything else right first. A rescued zero-match file claims every page it has, since
    there is no partial match to anchor a narrower page range to.

    Regex first (free, instant, and enough for wording already seen); a file that already
    matched something else but matches nothing by regex for the remaining type falls back to
    a narrow, targeted AI check (_ai_verify_second_type) - so a genuinely combined document is
    not missed just because its particular company's wording is not yet in the pattern list.
    That second-opinion call is deliberately NOT attempted for a zero-match file: its own
    wording ("this file was already found to contain X, does it ALSO contain Y") assumes an
    existing match to anchor to, which a zero-match file has none of.

    Signature lookups here go through _effective_doc_type, not a candidate's raw doc_type -
    a slot typed "Custom" but named "Fright Certificate" must still be checked against the
    Freight signature, exactly as its own description already gets the Freight hint from that
    same name (_hint_from_name). Keying this purely off the structured field silently checked
    nothing for any tenant whose slot was ever set up this way, which is most of them.
    """
    key_to_doctype = {c["key"]: _effective_doc_type(c) for c in candidates}
    cand_by_doctype: dict[str, dict] = {
        et: c for c in candidates if (et := _effective_doc_type(c)) is not None
    }
    doctype_to_keys: dict[str, list[str]] = {}
    for c in candidates:
        et = _effective_doc_type(c)
        if et is not None:
            doctype_to_keys.setdefault(et, []).append(c["key"])

    for i, (item, ms) in enumerate(zip(files, claims)):
        text = item.get("text") or ""
        if not text.strip():
            continue
        claimed_doctypes = {key_to_doctype.get(m["key"]) for m in ms}
        matched_pages = (
            sorted({p for m in ms for p in m["pages"]})
            if ms
            else list(range(1, (item.get("page_count") or 1) + 1))
        )
        for doc_type, keys in doctype_to_keys.items():
            if doc_type in claimed_doctypes:
                continue
            # Only types this feature actually covers by regex (PackingList, Invoice,
            # Freight) - a template's OTHER unclaimed slots (BL, a bespoke Custom type, ...)
            # have no pattern list to check and must not trigger an AI call just because they
            # happen to still be unclaimed.
            if doc_type not in _TYPE_SIGNATURES:
                continue
            is_match, hits = _keyword_signature_match(text, doc_type)
            if is_match:
                claims[i].append({
                    "key": keys[0], "pages": matched_pages,
                    # Deterministic evidence, not a judgement call - see the
                    # specificity rule, which refuses to strip a claim carrying this.
                    "source": "signature",
                    "evidence": f"keyword signature ({hits} supporting markers): the "
                                f"document's own text carries strong {doc_type} markers "
                                + ("alongside its other content" if ms else
                                   "that the model's own classification call missed entirely"),
                })
                logger.info(
                    "classify %s: added %s by keyword backstop (%d supporting markers)%s",
                    item.get("name"), doc_type, hits,
                    "" if ms else " - the model's own call matched nothing at all for this file")
                continue

            if not ms:
                # No existing match to anchor "does it ALSO contain" to - see the function's
                # own docstring for why the AI second-opinion call is skipped here.
                continue
            verified = _ai_verify_second_type(
                item.get("name") or "", text, item.get("image"),
                cand_by_doctype[doc_type], sorted(t for t in claimed_doctypes if t))
            if verified is None:
                continue
            claims[i].append({
                "key": keys[0], "pages": verified["pages"] or matched_pages,
                "source": "ai_second",
                "evidence": f"AI-verified combined document: {verified['evidence']}",
            })
            logger.info(
                "classify %s: added %s by AI second-type verification - the model's own "
                "call only recognised the other type on this page, and the regex backstop "
                "did not recognise this wording either",
                item.get("name"), doc_type)


def _classify_via_openai(filename: str, ocr_text: str | None, image_b64: str | None, candidates: list[dict]) -> list[str]:
    settings = get_settings()
    if not settings.openai_api_key or not candidates:
        return []

    # Which fields belong to ONE candidate only. A freight certificate slot listing "HBL No,
    # package_count, Gross Wt, Supplier Name, Supplier Address, Misc Charge Amount, Freight
    # Amount" reads mostly like a Bill of Lading, because five of those seven are on the BL as
    # well. The two that are not are the entire point, so they are called out separately -
    # otherwise a real freight certificate goes unrouted while the description invites a match
    # on the wrong slot.
    seen_in: dict[str, int] = {}
    for c in candidates:
        for f in set(c.get("fields") or []):
            seen_in[f] = seen_in.get(f, 0) + 1

    def _describe(c: dict) -> str:
        parts = []
        # THE SAMPLE FIRST. When the template was created this customer uploaded a real document
        # for this slot, so we know exactly what THEIR version looks like - their forwarder's
        # wording, their layout. That beats any generic description: a freight certificate
        # headed ARRIVAL NOTICE matches the sample at once and matches the words "freight
        # certificate" not at all.
        ref = (c.get("reference") or "").strip()
        if ref:
            parts.append("THE CUSTOMER'S OWN SAMPLE of this document, uploaded when their "
                         f'template was set up: "{ref[:1200]}"')
        examples = _describe_examples(c)
        if examples:
            parts.append(examples)
        hint = DOC_TYPE_HINTS.get(c["doc_type"]) or _hint_from_name(c.get("name") or "")
        if hint:
            parts.append(f"typical content: {hint}")
        fields = [f for f in (c.get("fields") or []) if f]
        only_here = [f for f in fields if seen_in.get(f) == 1]
        if only_here:
            parts.append("fields found ONLY on this document: " + ", ".join(only_here[:12]))
        shared = [f for f in fields if seen_in.get(f, 0) > 1]
        if shared:
            parts.append("also carries (shared with other documents, so not distinguishing): "
                         + ", ".join(shared[:12]))
        if not parts:
            return "no content hint available - match on the document name alone"
        return "; ".join(parts)

    cand_lines = "\n".join(
        f'- key="{c["key"]}" name="{c["name"]}" type={c["doc_type"]} ({_describe(c)})'
        for c in candidates
    )
    instruction = (
        "You classify a logistics/customs document. Decide which of the expected document "
        "types below this file matches, judging mainly by its CONTENT (the filename is a weak "
        "hint). A single file can be a COMBINED document that matches MORE THAN ONE type — "
        "either mixed on the same pages, OR different types on different pages (e.g. page 1 is "
        "a Bill of Lading and page 2 is a Packing List). The text below is labelled per page "
        "('=== PAGE n ==='); inspect EVERY page and include every type present on ANY page. "
        "If it matches none, return an empty list.\n\n"
        "Where a type shows THE CUSTOMER'S OWN SAMPLE, compare against it directly — that is a "
        "real document of this type from this same customer, so it is the best evidence you "
        "have. Match on what the document IS, not on what it is titled: a slot called 'Freight "
        "Certificate' is still the right home for a document headed ARRIVAL NOTICE, if that is "
        "where this customer's freight charges appear.\n\n"
        f"Expected types:\n{cand_lines}\n\n"
        f'Filename: {filename}\n\n'
        'Return a JSON object: {"keys": [ ...matching key strings... ]}.'
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        if ocr_text and ocr_text.strip():
            user_content: object = instruction + "\n\n--- DOCUMENT TEXT ---\n" + ocr_text[:8000]
        elif image_b64:
            user_content = [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]
        else:
            user_content = instruction
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": user_content}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        keys = data.get("keys") or []
        valid = {c["key"] for c in candidates}
        kept = [k for k in keys if k in valid]
        if not kept:
            # It read the document and matched nothing. Worth saying out loud: the alternative
            # is an empty slot with no explanation anywhere, which is what made a missing
            # freight certificate impossible to diagnose.
            logger.info("classify %s: the analyser matched no slot (it was given %s chars of "
                        "text, %s candidates)", filename, len(ocr_text or ""), len(candidates))
        elif len(kept) != len(keys):
            logger.info("classify %s: dropped %s key(s) the model invented",
                        filename, len(keys) - len(kept))
        return kept
    except Exception as exc:  # noqa: BLE001
        logger.warning("Classification failed for %s: %s", filename, exc)
        return []


def classify_file(filename: str, ocr_text: str | None, image_path: Path | None, candidates: list[dict]) -> list[str]:
    """Returns the candidate keys this file matches (possibly several for a combined doc)."""
    image_b64 = None
    if (not ocr_text or not ocr_text.strip()) and image_path and image_path.exists():
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
    return _classify_via_openai(filename, ocr_text, image_b64, candidates)


# ---------------------------------------------------------------------------------------------
# WHICH CUSTOMER is this? Decided from the documents' own words, not from who sent the email.
#
# The sender address is a weak signal in real life: a forwarding agent mails on behalf of six
# importers from one address, a customer mails from whatever laptop is to hand, and a new
# customer's first email arrives before anyone has configured an address for them. The documents
# themselves are not vague about it - a Bill of Lading names its consignee, an invoice names its
# buyer - so that is what decides.
# ---------------------------------------------------------------------------------------------

# Fields whose configured value identifies the customer rather than describing the shipment. A
# consignee name, an IE code or an AD code is the same on every job for that customer, which is
# exactly what makes it usable as a fingerprint.
IDENTIFYING_FIELDS = (
    "consignee", "importer", "buyer", "ie_code", "iec", "gstin", "ad_code", "branch",
)


def customer_fingerprints(groups: list[dict]) -> list[dict]:
    """Reduce each candidate customer to the words its documents would carry.

    `groups` items: {"key", "name", "entry_mode", "identifiers": {label: value},
                     "doc_types": [...]}. Returned unchanged apart from dropping any candidate
    with nothing to match on - offering the model a customer it cannot possibly recognise only
    invites a guess.
    """
    out = []
    for g in groups:
        ids = {k: v for k, v in (g.get("identifiers") or {}).items() if str(v or "").strip()}
        if not ids:
            continue
        out.append({**g, "identifiers": ids})
    return out


def identify_customer(mail_subject: str, mail_body: str, doc_texts: list[tuple[str, str]],
                      groups: list[dict]) -> dict:
    """Which customer do these documents belong to?

    Returns {"keys": [...], "reason": str, "evidence": str}. `keys` may be:
      empty      - nothing here names a customer we know
      one        - a confident match
      several    - genuinely ambiguous; the caller decides what to do about it

    Never invents a key: the result is filtered against the candidates given.
    """
    settings = get_settings()
    cands = customer_fingerprints(groups)
    if not settings.openai_api_key or not cands:
        return {"keys": [], "reason": "no candidates with identifying data", "evidence": ""}

    lines = []
    for c in cands:
        ids = "; ".join(f"{k}={v!r}" for k, v in c["identifiers"].items())
        lines.append(f'- key="{c["key"]}" name="{c["name"]}" identified by: {ids}')

    doc_blob = "\n\n".join(
        f"--- DOCUMENT: {name} ---\n{(text or '')[:6000]}" for name, text in doc_texts
    )[:24000]

    instruction = (
        "You are routing incoming shipping documents to the importer they belong to.\n\n"
        "Below are the importers we handle, each with the values that identify it - a consignee "
        "name, an IE code, a GST number, a branch. Read the attached documents and decide which "
        "importer these belong to.\n\n"
        "Rules:\n"
        "1. Decide from the DOCUMENTS. The email's wording is a hint, not evidence.\n"
        "2. The consignee / importer / buyer on a Bill of Lading or invoice is the strongest "
        "signal. A shipper or supplier name is NOT - that is the other end of the shipment.\n"
        "3. Company names vary in writing: 'NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD' and "
        "'Nokia Solutions & Networks India Private Limited' are the same importer. An IE code or "
        "GST number matching is conclusive.\n"
        "4. If the documents do not name any importer on this list, return an EMPTY list. Do not "
        "pick the closest one. A wrong answer files a customs declaration against the wrong "
        "company, which is far worse than no answer.\n"
        "5. If several importers on the list fit equally and nothing in the documents separates "
        "them, return ALL of them rather than choosing.\n\n"
        f"Importers:\n" + "\n".join(lines) + "\n\n"
        f"Email subject: {mail_subject}\n"
        f"Email body:\n{(mail_body or '')[:2000]}\n\n"
        f"{doc_blob}\n\n"
        'Return JSON: {"keys": [...], "reason": "one sentence", '
        '"evidence": "the exact words in the documents that decided it"}'
    )
    try:
        from openai import APIError, OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=60)
        resp = client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=400,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": instruction}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        valid = {c["key"] for c in cands}
        keys = [k for k in (data.get("keys") or []) if k in valid]
        return {
            "keys": keys,
            "reason": str(data.get("reason") or "")[:400],
            "evidence": str(data.get("evidence") or "")[:800],
        }
    except APIError as exc:
        # Quota exhausted, rate limited, a dropped connection, a 5xx - the documents were
        # never actually read, so this is NOT "no customer identified". See AIServiceUnavailable.
        raise AIServiceUnavailable(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("customer identification failed: %s", exc)
        return {"keys": [], "reason": f"identification failed: {exc}"[:300], "evidence": ""}


# ---------------------------------------------------------------------------------------------
# ASSIGNING FILES TO SLOTS. Classifying each file on its own is not enough, and four real jobs
# show why: one invoice filled BOTH the Invoice and the Packing List slot, and once the freight
# slot was described better, one bill of lading filled BOTH the BL and the Freight slot. The
# real packing list and the real freight certificate were dropped on the floor.
#
# The cause was never really the model. Every key it returned was written into a slot, with no
# requirement that the document type actually be PRESENT and no contest when two files claimed
# the same slot. A shipment's paperwork is full of near-identical documents - a BL, an arrival
# notice and a packing list all carry the same B/L number, consignee, weights and package count
# - so "resembles" has to be separated from "is", and a slot has to go to its best claimant.
#
# Two rules, both deterministic, both applied after the model has spoken:
#
#   1. EVIDENCE. A match counts only if the model can say which page it is on and quote the
#      content that makes it that type. No evidence, no match.
#   2. SPECIFICITY. When several files claim one slot, the file that claimed the FEWEST slots
#      wins it. A file that says "I am an invoice" beats one that says "I am an invoice and
#      also a packing list", and the loser keeps whatever else it claimed.
# ---------------------------------------------------------------------------------------------


def classify_document(filename: str, ocr_text: str | None, image_path: Path | None,
                      candidates: list[dict]) -> list[dict]:
    """Which document types are PRESENT in this file, with the evidence for each.

    Returns [{"key", "pages": [int], "evidence": str}]. A match the model cannot back with
    evidence is dropped here rather than trusted: that is the whole difference between "this
    file contains a packing list" and "this file looks a bit like one".
    """
    settings = get_settings()
    if not settings.openai_api_key or not candidates:
        return []
    if not ocr_text or not ocr_text.strip():
        # No extractable text at all - never worth an AI opinion. A page with nothing to
        # quote as evidence must not be attached to a job as if a vision guess from its raw
        # appearance were a real match; that determination stays with OCR, not AI.
        logger.info("classify %s: no OCR text extracted - not asking the model to guess "
                    "from the image; this file is not attached to any document slot", filename)
        return []

    seen_in: dict[str, int] = {}
    for c in candidates:
        for f in set(c.get("fields") or []):
            seen_in[f] = seen_in.get(f, 0) + 1

    def describe(c: dict) -> str:
        parts = []
        ref = (c.get("reference") or "").strip()
        if ref:
            parts.append("the customer's own sample of this document: " + repr(ref[:900]))
        examples = _describe_examples(c)
        if examples:
            parts.append(examples)
        hint = DOC_TYPE_HINTS.get(c["doc_type"]) or _hint_from_name(c.get("name") or "")
        if hint:
            parts.append(f"typical content: {hint}")
        fields = [f for f in (c.get("fields") or []) if f]
        only_here = [f for f in fields if seen_in.get(f) == 1]
        if only_here:
            parts.append("content found ONLY on this type: " + ", ".join(only_here[:12]))
        return "; ".join(parts) or "no hint available"

    lines = "\n".join(
        f'- key="{c["key"]}" name="{c["name"]}" type={c["doc_type"]} ({describe(c)})'
        for c in candidates)

    instruction = (
        "You are sorting the documents in one shipment into the slots below.\n\n"
        "For the file given, decide which of these document types it ACTUALLY CONTAINS. Judge "
        "by content; the filename is a weak hint.\n\n"
        "This is the hard part, so read it carefully. A shipment's paperwork looks alike: a "
        "bill of lading, an arrival notice and a packing list all carry the same B/L number, "
        "the same consignee, the same weights and package counts. RESEMBLING a type is not the "
        "same as BEING it. Return a type only if the document that defines it is present - a "
        "packing list has a per-package breakdown, an invoice has prices and a total payable, "
        "a freight certificate has freight charges, a bill of lading is the carrier's contract "
        "of carriage.\n\n"
        "ONE SPECIFIC TRAP: a bill of lading or sea waybill is very often followed, in the SAME "
        "file, by the carrier's OWN continuation page - typically headed \"ATTACHMENT FOR\" or "
        "\"CONTINUED\" plus that same B/L or waybill number, still on the carrier's letterhead - "
        "listing marks & numbers, package count and weight against that number. That page's "
        "content looks exactly like a packing list's (a per-package weight/count breakdown), but "
        "it is NOT one: it is the carrier's own document, continuing the bill of lading/waybill "
        "from the page before it. Return that page as part of the SAME bill of lading entry, "
        "never as a separate Packing List. A genuine packing list is prepared by the shipper, not "
        "the carrier - it stands as its own document, on its own letterhead, and does not open by "
        "referencing a B/L or waybill number the way a carrier's continuation page does.\n\n"
        "ANOTHER SPECIFIC TRAP: a commercial/export INVOICE's own header often prints \"Net "
        "Weight\" and \"Gross Weight\" FIELDS - sometimes filled in, sometimes left blank - "
        "right alongside its invoice number, buyer and Incoterm. Printing those two field "
        "LABELS does not make the page a packing list. A packing list is defined by an actual "
        "PER-PACKAGE OR PER-LINE breakdown - a table of individual cartons/pieces each with "
        "their own weight/dimension row, or a marks-and-numbers listing. A page with prices, "
        "an invoice number, unit values and a total payable is an Invoice ONLY, even when a "
        "\"Net Weight\"/\"Gross Weight\" field sits on the same page - do not also return "
        "\"PackingList\" for it on that basis alone.\n\n"
        "A THIRD SPECIFIC TRAP: a ONE-ROW cargo summary - marks & numbers, a package count, ONE "
        "gross weight, ONE measurement figure, all on a single line - is NOT, by itself, "
        "evidence of a packing list. This exact one-row summary is COMMON to many different "
        "documents in the same shipment's paperwork: a bill of lading's own \"Particulars "
        "Furnished by Shipper\" box has it, and so, separately, does a forwarder's arrival "
        "notice, a checklist, or a delivery order - often quoting the very same figures copied "
        "from one document to another, because they describe the same shipment, not because "
        "either one IS a packing list. A genuine packing list is defined by a PER-CARTON OR "
        "PER-LINE breakdown - several rows, each its own package with its own weight - never by "
        "a single shipment-level total, wherever that total is printed.\n\n"
        "This cuts BOTH ways: do not return \"PackingList\" for a page on the strength of its "
        "own one-row cargo summary alone, AND do not use that same one-row summary as evidence "
        "that a page IS the Bill of Lading either - an arrival notice or checklist carrying the "
        "identical figures is not the bill of lading just because it repeats them. Identify the "
        "Bill of Lading itself by ITS OWN distinguishing structure - a Shipper and Consignee "
        "box, a Notify Party, the carrier's own terms/clauses of carriage, a document actually "
        "titled Bill of Lading, Sea Waybill or Waybill - never by the cargo summary line alone, "
        "since other documents in the same shipment routinely repeat that exact line.\n\n"
        "Return one entry per PHYSICALLY DISTINCT document you find in the file, not one per "
        "type. A file can hold several documents of the very same type - three invoices scanned "
        "into one PDF is three entries with key=\"Invoice\", not one entry covering all their "
        "pages - exactly the same way it can hold different types on different pages (page 1 a "
        "bill of lading, page 2 a packing list). Two entries may repeat the same key; they must "
        "never share a page - a page belongs to exactly one document. Tell separate instances "
        "apart by what actually changes between them (a new invoice/document number, a new "
        "date, the page restarting a header), not by page count alone. If it is one document, "
        "return exactly one entry. If it matches none, return an empty list; that is a "
        "perfectly good answer and far better than a wrong slot.\n\n"
        "A COMBINED DOCUMENT SPANS ALL ITS OWN PAGES, FOR EVERY TYPE IT MATCHES. A customs "
        "house agent's own \"Checklist\"/\"Shipping Bill Checklist\" is very often ONE document "
        "that is both the Invoice and the Packing List at once - a commercial-terms page "
        "(invoice number, buyer, FOB value) immediately followed by that SAME shipment's item "
        "table (quantities, HS codes) with no new header, no new document number, nothing "
        "restarting. That is still one document, not two - the same test as above (a new "
        "number, a new date, a restarting header) tells you it never actually ended. When it "
        "matches a type, give that entry EVERY page of the combined document, not only the "
        "page where that type's own defining evidence happened to be quoted from - an Invoice "
        "match whose evidence is a price on page 1 still covers page 2 if page 2 is the same "
        "shipment's item table with no new document starting there. A slot missing the very "
        "page that carries its quantities/weights, because that page's defining evidence "
        "happened to belong to a different type, is the mistake this guards against.\n\n"
        "For every entry you return you MUST give the page(s) it is on and quote the content "
        "that makes it that type. A match you cannot quote will be discarded.\n\n"
        f"Slots:\n{lines}\n\n"
        f"Filename: {filename}\n\n"
        'Return JSON: {"matches": [{"key": "...", "pages": [1], '
        '"evidence": "the words on the page that make it this type"}]}'
    )
    try:
        from openai import APIError, OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=60)
        content: object = instruction + "\n\n--- DOCUMENT TEXT ---\n" + ocr_text[:9000]
        resp = client.chat.completions.create(
            model=settings.openai_model,
            # Was 600 - a file with several instances of the same type (three invoices in
            # one PDF) now returns one entry per instance instead of one per type, and got
            # cut off mid-JSON on a genuinely multi-instance file at the old limit.
            max_tokens=1200,
            # A shipment's several near-identical documents (five packing lists from the
            # same shipper) were classified ONE FILE AT A TIME, each its own independent call
            # with no temperature pinned - two structurally identical files could and did come
            # back with different answers purely from sampling, with nothing in the content
            # itself explaining why one specific instance landed on the wrong slot.
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": content}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        valid = {c["key"] for c in candidates}
        out = []
        for m in data.get("matches") or []:
            key = m.get("key")
            ev = str(m.get("evidence") or "").strip()
            if key not in valid:
                continue
            if len(ev) < 8:
                logger.info("classify %s: dropped a match on %s with nothing to back it",
                            filename, key)
                continue
            pages = [int(p) for p in (m.get("pages") or []) if str(p).isdigit()]
            out.append({"key": key, "pages": pages or [1], "evidence": ev[:300],
                        "source": "model"})
        if not out:
            logger.info("classify %s: no slot's document is present in this file (%s chars of "
                        "text, %s slots offered)", filename, len(ocr_text or ""),
                        len(candidates))
        return out
    except APIError as exc:
        # Quota exhausted, rate limited, a dropped connection, a 5xx - the model was never
        # actually consulted, so this file has NOT been found to match nothing; it has not
        # been looked at. See AIServiceUnavailable.
        raise AIServiceUnavailable(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("Classification failed for %s: %s", filename, exc)
        return []


def assign_documents_detailed(files: list[dict], candidates: list[dict]) -> list[list[dict]]:
    """Same as assign_documents, but keeps each match's page numbers instead of collapsing
    to a bare key: [{"key", "pages": [int], "evidence": str}] per file.

    The pages are in the file's own KEPT-page numbering - position within the "=== PAGE n
    ===" text the classifier was actually shown, after carrier-terms pages were filtered out
    - not the original PDF's page numbers. A caller that wants to physically split the PDF
    needs to translate through whatever page-drop list it used to build that text (see
    page_filter.extract_pdf_pages and its callers).
    """
    claims: list[list[dict]] = [
        classify_document(f.get("name") or "", f.get("text"), f.get("image"), candidates)
        for f in files
    ]

    # A deterministic backstop, run BEFORE the reconciliation below so an added claim is
    # reconciled exactly like one the model found itself. See _augment_combined_document_claims.
    _augment_combined_document_claims(files, candidates, claims)

    # 1. The SAME key may never claim a page twice - a file can genuinely hold several
    #    invoices, and each is its own document with its own, non-overlapping pages, so only
    #    an actual page clash WITHIN one type means one of those claims is wrong.
    #
    #    DIFFERENT keys sharing a page are a different case and are deliberately NOT deduped
    #    here: a "SHIPPING INVOICE CUM PACKING LIST" is one real page that genuinely is both
    #    an Invoice and a Packing List at once - a combined document, not a classifier
    #    mistake. Rejecting that page for one of the two types (the earlier version of this
    #    rule did) meant a genuinely combined document always lost one of its two slots, when
    #    what it needs is the SAME file written into BOTH.
    for i, ms in enumerate(claims):
        if len(ms) <= 1:
            continue
        pages_used_by_key: dict[str, set[int]] = {}
        kept = []
        for m in sorted(ms, key=lambda x: -len(x["evidence"])):
            used = pages_used_by_key.setdefault(m["key"], set())
            if set(m["pages"]) & used:
                logger.info("classify %s: dropped a second %s claiming a page already taken "
                            "by another instance of the same type", files[i].get("name"), m["key"])
                continue
            used |= set(m["pages"])
            kept.append(m)
        claims[i] = kept

    # 2. A slot may hold SEVERAL files — a shipment covered by three invoices is three files
    #    in the Invoice slot — but only files that claimed it just as specifically. Specificity
    #    is how many DISTINCT TYPES the file claimed, not how many matches - a file with three
    #    separate invoices and nothing else is still a single-type (specific) claimant, not a
    #    broad one, and must not lose the Invoice slot to a file with only one invoice on it.
    #    A file saying only "packing list" still beats one saying "invoice AND packing list",
    #    and the broad claimant is dropped from that slot exactly as before.
    #
    #    This used to read "one slot, one file", and the most specific single claimant won.
    #    That is what silently discarded invoices 2 and 3: three files each saying "invoice"
    #    and only one of them kept. Keeping every EQUALLY specific claimant fixes that without
    #    reopening the original bug, where one invoice filled the packing list slot as well and
    #    the real packing list was dropped.
    #    One exception, and it matters: a claim the keyword backstop added is NOT stripped
    #    here. Breadth is a heuristic about how broadly a file claimed; a keyword signature is
    #    the document's own words matching a required phrase plus supporting markers. When
    #    those disagree the evidence wins.
    #
    #    Without the exception this rule did the opposite of its job. A forwarder's arrival
    #    notice was wrongly claimed as an Invoice by the model (it carries "Total Payable" and
    #    a charge table) and correctly claimed as Freight by the backstop. Holding two slots
    #    made it "broad", so it lost Freight to a file holding one - and kept the Invoice
    #    claim, the wrong one, because only the well-evidenced claim was eligible to be
    #    stripped. The arrival notice then sat in the Invoice slot and the real invoice had to
    #    share it. Stripping the evidenced claim and keeping the guess is exactly backwards.
    breadth = [len({m["key"] for m in ms}) for ms in claims]
    for cand in candidates:
        key = cand["key"]
        holders = [i for i, ms in enumerate(claims) if any(m["key"] == key for m in ms)]
        if len(holders) <= 1:
            continue
        keenest = min(breadth[i] for i in holders)
        winners = [i for i in holders if breadth[i] == keenest]
        for i in holders:
            if i not in winners:
                if any(m["key"] == key and m.get("source") == "signature"
                       for m in claims[i]):
                    logger.info(
                        "classify: %s keeps %r despite claiming %s slot(s) - its own text "
                        "carries the keyword signature for it",
                        files[i].get("name"), cand.get("name"), breadth[i])
                    continue
                claims[i] = [m for m in claims[i] if m["key"] != key]
                logger.info("classify: %s loses %r (it claimed %s slot(s); %s claimed only %s)",
                            files[i].get("name"), cand.get("name"), breadth[i],
                            " and ".join(str(files[w].get("name")) for w in winners), keenest)
        if len(winners) > 1:
            logger.info("classify: %r holds %d files — %s", cand.get("name"), len(winners),
                        ", ".join(str(files[w].get("name")) for w in winners))
    return claims


def assign_documents(files: list[dict], candidates: list[dict]) -> list[list[str]]:
    """Sort a shipment's files into slots, one slot to one file.

    `files`: [{"name", "text", "image"}]. Returns, per file and in the same order, the slot
    keys it should be written into - after the two rules above have been applied.
    """
    return [[m["key"] for m in ms] for ms in assign_documents_detailed(files, candidates)]

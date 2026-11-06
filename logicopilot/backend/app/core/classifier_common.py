"""Classification infrastructure shared across every shipping mode's own classifier module
(classifier_sea_import.py, classifier_air_import.py, classifier_sea_export.py,
classifier_air_export.py) - never duplicated, because what lives here either behaves
identically regardless of mode, or runs BEFORE a shipment's own mode is even known.

AIServiceUnavailable is a plain exception type: every mode's own module raises and catches
the SAME class, so a caller never needs to know (or check) which mode's module actually threw
it.

identify_customer/customer_fingerprints/IDENTIFYING_FIELDS decide WHICH TEMPLATE a shipment's
documents belong to, among a tenant's several templates - the step that runs before any
per-document classification, and before this shipment's own shipping mode is even known. It
cannot be split per mode without first knowing the mode, so it stays here, shared.

classifier_module(mode) is how every caller picks which mode's own module actually does the
per-document classification work - see its own docstring below.
"""

import json
import logging

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


def classifier_module(mode: str | None):
    """Picks the classifier module for this shipment's own shipping mode (see
    app/core/modes.py for the 4 valid values). Each mode has its own fully independent copy of
    the per-document classification pipeline (classify_document, assign_documents_detailed,
    ...) - tuning or fixing one mode's classification can never change another's, by
    construction, since they are not the same code.

    Falls back to Sea Import - the longest-trained, most heavily used module - for a mode with
    no jobs of its own yet (Sea Export / Air Export, as of when this was written) or one that's
    missing/unrecognised, so an unset/unexpected mode never crashes a job outright.
    """
    import importlib

    name = {
        "Sea Import": "classifier_sea_import",
        "Air Import": "classifier_air_import",
        "Sea Export": "classifier_sea_export",
        "Air Export": "classifier_air_export",
    }.get(mode, "classifier_sea_import")
    return importlib.import_module(f"app.core.{name}")


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
        from openai import APIError

        from app.core.llm import create_chat_completion_with_retry

        resp = create_chat_completion_with_retry(
            timeout=60,
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

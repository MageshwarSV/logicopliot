"""OpenAI service — turns a single marked field into a robust extraction profile.

The Super Admin marks ONE region on ONE sample document and names it (e.g. marks
"Seaway Bill of Lading No" and calls it `bl_number`). But real documents label the
same value many ways: "Seaway Bill No", "Waybill No", "B/L No", or just "No". So
from that one mark we generate a *profile* that generalizes:

- anchor_variations:    every caption the field realistically appears under
- semantic_description: what the value means (for the semantic fallback at extraction)
- value_format_hint:    the expected shape of the value
- extraction_prompt:    the assembled instruction

If OpenAI is unreachable we fall back to a deterministic local profile so marking
never hard-fails on the network.
"""

import json
import logging
import re
import time
from dataclasses import dataclass

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# OpenAI's own suggested wait ("Please try again in 372ms." / "in 1.844s.") isn't exposed as
# a separate field on the exception - only inside its message text - so it's parsed out here.
_RETRY_WAIT_RE = re.compile(r"try again in ([\d.]+)\s*(ms|s)\b")
_MAX_RATE_LIMIT_RETRIES = 4


def _rate_limit_wait_seconds(exc: Exception) -> float | None:
    """None means "raise immediately, do not retry": either this isn't a rate-limit error at
    all, or it's the one 429 that retrying can never fix - an exhausted prepaid balance
    (`exc.code == "credit_balance_exhausted"`) stays exhausted no matter how long you wait.
    Only a genuine `code == "rate_limit_exceeded"` (a per-minute token/request cap, which
    resets on its own) gets a wait, taken from OpenAI's own suggested delay in its error
    message - `.code` is the SDK's own parse of the response body's "code" field, confirmed
    by reading openai._client.OpenAI._make_status_error rather than assumed."""
    if getattr(exc, "code", None) != "rate_limit_exceeded":
        return None
    match = _RETRY_WAIT_RE.search(str(exc))
    if not match:
        return 1.0
    value, unit = match.groups()
    return float(value) / 1000.0 if unit == "ms" else float(value)


def create_chat_completion_with_retry(client, **kwargs):
    """The one place every OpenAI chat-completion call in this codebase goes through, instead
    of calling `client.chat.completions.create(**kwargs)` directly. Every caller here used to
    catch ANY exception from that bare call and silently degrade to an empty/default value,
    with no retry at all - exactly right for a genuine error (a bad request, an empty
    balance, a real outage), but wrong for a rate_limit_exceeded 429: a routine,
    SELF-CORRECTING condition (the per-minute token budget resets within seconds) that this
    codebase's own call volume can trigger under ordinary load - one job's custom fields
    alone can fire 70+ separate chat-completion calls in quick succession. Found live: on a
    real job, several fields were silently written as empty because their calls happened to
    land in exactly such a window - indistinguishable in the UI from "the AI found nothing on
    the document" (see _rate_limit_wait_seconds's own docstring for how this is told apart
    from an error no retry can fix).

    Retries up to `_MAX_RATE_LIMIT_RETRIES` times, waiting the exact time OpenAI's own error
    message suggests each time, and ONLY for a genuine rate_limit_exceeded - every other
    error, including insufficient_quota/credit_balance_exhausted, is raised immediately
    exactly as before this existed."""
    from openai import RateLimitError

    last_exc: RateLimitError | None = None
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            wait = _rate_limit_wait_seconds(exc)
            if wait is None or attempt == _MAX_RATE_LIMIT_RETRIES:
                raise
            last_exc = exc
            time.sleep(wait)
    raise last_exc  # pragma: no cover - the loop above always returns or raises


@dataclass
class FieldProfile:
    anchor_variations: list[str]
    semantic_description: str
    value_format_hint: str
    extraction_prompt: str


def _fallback(field_name: str, anchor: str | None, value: str | None) -> FieldProfile:
    variations = [v for v in {anchor, field_name.replace("_", " ")} if v]
    anchor_line = f' It usually appears next to a caption like "{anchor}".' if anchor else ""
    value_line = f' In the reference document the value was "{value}".' if value else ""
    return FieldProfile(
        anchor_variations=variations,
        semantic_description=f'The document field "{field_name}".',
        value_format_hint=value or "",
        extraction_prompt=(
            f'Find the value for "{field_name}".{anchor_line}{value_line} '
            f"Different documents may label it differently, so match by meaning as well as "
            f"by caption. Return only the raw value, with no caption text or explanation."
        ),
    )


def evaluate_ruling(
    ruling_prompt: str,
    all_document_names: list[str],
    documents_text: str,
    operator_answer: str | None = None,
) -> dict:
    """Decide which of a template's documents are required for THIS job, per the Super
    Admin's ruling (e.g. incoterm-based document requirements). Returns JSON:
      {"required_documents": [names...], "decision": <what it found, e.g. incoterm>,
       "needs_input": bool, "question": <what to ask the operator if it can't decide>,
       "reason": str}. If `operator_answer` is provided, use it to resolve the decision."""
    settings = get_settings()
    if not settings.openai_api_key:
        return {"required_documents": all_document_names, "decision": None, "needs_input": False,
                "question": "", "reason": "AI not configured — requiring all documents."}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=350,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You decide which documents a shipping/data-entry job actually requires, by "
                        "applying the supervisor's rule to the uploaded documents' content. You are "
                        "given: the rule, the full list of the template's document names, and the text "
                        "of the uploaded documents. Determine the deciding value the rule depends on "
                        "(e.g. the incoterm) and return the required subset. If you CANNOT determine it "
                        "from the documents, set needs_input=true and put the exact question to ask the "
                        "operator in 'question'. If an operator answer is supplied, use it. "
                        'Reply with JSON: {"required_documents": [<names from the given list>], '
                        '"decision": <string or null>, "needs_input": <bool>, "question": <string>, '
                        '"options": [<the choices the operator should pick from, when needs_input '
                        'is true — e.g. the incoterm codes named in the rule; empty otherwise>], '
                        '"reason": <short>}. required_documents must only contain names from the '
                        "provided list."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Rule: {ruling_prompt}\n"
                        f"Template document names: {all_document_names}\n"
                        + (f"Operator's answer to the earlier question: {operator_answer}\n" if operator_answer else "")
                        + f"Uploaded documents text:\n{documents_text[:48000]}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        req = [n for n in (data.get("required_documents") or []) if n in all_document_names]
        if not req and not data.get("needs_input"):
            req = all_document_names  # safe default: require everything
        options = data.get("options") or []
        if not isinstance(options, list):
            options = [options]
        return {
            "required_documents": req,
            "decision": data.get("decision"),
            "needs_input": bool(data.get("needs_input")),
            "question": data.get("question") or "",
            # What to offer the operator when it cannot decide — rendered as a dropdown.
            "options": [str(o) for o in options if str(o).strip()],
            "reason": data.get("reason") or "",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("evaluate_ruling failed: %s", exc)
        return {"required_documents": all_document_names, "decision": None, "needs_input": False,
                "question": "", "options": [], "reason": f"ruling evaluation error: {exc}"}


def describe_failure_screen(screenshot_b64: str, log_tail: list[str] | None = None) -> str:
    """Look at the screen where the run stopped and say, in plain words, what happened.

    When a script fails on a screen it does not recognise, the log only says which selector
    was missing — useless to an operator. The screenshot is the only thing that actually knows
    what went wrong: a session-expired banner, a validation error, a maintenance page. This
    reads it and writes the sentence the operator needs on their Failed screen.

    Returns "" when unavailable, so the caller keeps its existing reason.
    """
    settings = get_settings()
    if not settings.openai_api_key or not screenshot_b64:
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        tail = "\n".join((log_tail or [])[-6:])
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            temperature=0,
            max_tokens=220,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "An automated data-entry run stopped on this ERP screen. Describe what "
                        "is on the screen and why the automation could not continue, in 1-3 "
                        "plain sentences an operator can act on. Quote any error or warning "
                        "message you can see, verbatim. Say what the operator should do next if "
                        "it is clear. Do NOT mention selectors, code, or automation internals.\n\n"
                        f"Last steps attempted:\n{tail}"
                    )},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                ],
            }],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — diagnosis is a bonus, never a failure path
        logger.warning("describe_failure_screen failed: %s", exc)
        return ""


def ai_classify_outcome(page_text: str, field_stats: dict | None = None) -> dict:
    """When an ERP form locks all its fields after a value is entered, decide WHAT the
    situation is by reading the page (no hardcoded field names or messages). Returns
    {"outcome": "duplicate" | "blocked" | "error" | "unknown", "reason": <short>}.
    'duplicate' = the record already exists; 'blocked' = the form is locked for another
    reason; 'error' = an error is shown."""
    settings = get_settings()
    if not settings.openai_api_key:
        return {"outcome": "blocked", "reason": "Form fields became read-only after entry."}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=20)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=150,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "An ERP data-entry form locked its fields right after a value was entered, so "
                        "it can't be submitted. Read the page text and decide why. Most often this means "
                        "the record ALREADY EXISTS (a duplicate) — the ERP recognised the value and "
                        "blocked re-entry. Reply with JSON: {\"outcome\": one of "
                        "\"duplicate\"|\"blocked\"|\"error\"|\"unknown\", \"reason\": <short human note>}."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Field stats: {field_stats}\n"
                        f"Page text:\n{(page_text or '')[:2500]}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        outcome = data.get("outcome")
        if outcome not in ("duplicate", "blocked", "error", "unknown"):
            outcome = "blocked"
        return {"outcome": outcome, "reason": data.get("reason") or ""}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_classify_outcome failed: %s", exc)
        return {"outcome": "blocked", "reason": "Form fields became read-only after entry."}


def compute_custom_field(prompt: str, documents_text: str, values: dict[str, str] | None = None,
                         records: list[dict] | None = None) -> str:
    """Compute a custom field's value from the selected documents, following the Super Admin's
    instruction (e.g. a calculation across documents).

    `records` is one compact JSON record per uploaded FILE — the structured reading this job
    already produced, complete for every document. `documents_text` is the raw text behind
    them, and is supporting evidence only.

    That split is what makes twenty documents work. Raw text does not fit: twenty invoices are
    roughly 49,000 characters and they were sharing a 40,000 budget, so each one lost its tail
    — which is exactly where a total sits. The same twenty as records are about 10,000
    characters, because a record carries the values and drops the letterhead, the addresses
    and the terms. Every document is read in full ONCE, on its own, and the field that has to
    reason across all of them reads the readings rather than the paper.

    `values` is kept for callers that pass a flat label->value dict, but it holds only the
    first document's value per label — with several invoices on a job it cannot represent
    them. Prefer `records`.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compute one field for a data-entry job by following an instruction. "
                        "You are given the instruction, one JSON record per uploaded document, "
                        "and the raw text behind them.\n\n"
                        "THE RECORDS ARE THE COMPLETE SET. Every document on this job appears "
                        "there, already read. A job can carry several invoices with their own "
                        "packing lists, paired by \"set\". When the instruction asks for a "
                        "total, a count, or anything covering the shipment, it means ACROSS "
                        "EVERY RECORD — not the first one. State it from the records; use the "
                        "raw text only to check a detail a record does not carry, and never "
                        "assume the raw text is complete, because it may be shortened.\n\n"
                        'Reply with JSON: {"value": <string>}. Return only the value.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Instruction: {prompt}\n\n"
                        + (f"Documents on this job ({len(records)} record(s), all of them):\n"
                           f"{json.dumps(records, ensure_ascii=False)}\n\n" if records else "")
                        + (f"Already-extracted values: {json.dumps(values, ensure_ascii=False)}\n\n"
                           if values and not records else "")
                        + f"Raw document text (supporting evidence, may be shortened):\n"
                        + documents_text[:48000]
                    ),
                },
            ],
        )
        return str(json.loads(resp.choices[0].message.content or "{}").get("value", "")).strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_custom_field failed: %s", exc)
        return ""


# A field could plausibly list hundreds of container numbers on a real consolidation, but
# nothing legitimate needs more than this — a cap here is what stops one bad response (or a
# document it misreads as one giant table) from writing thousands of JobFieldValue rows.
CUSTOM_FIELD_ROWS_LIMIT = 200


def compute_custom_field_rows(prompt: str, documents_text: str,
                               records: list[dict] | None = None) -> list[str]:
    """Like compute_custom_field, but for a field ticked "multiple values from document" —
    the instruction describes ONE item (a container number, a line on a packing list with no
    mark of its own) and this returns every instance actually found, instead of one value for
    the whole field. Mirrors a Mark ticked "multiple values in this document": the field is
    read directly off its own selected document(s), not tied to any other field's rows."""
    settings = get_settings()
    if not settings.openai_api_key:
        return []
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract EVERY instance of one field from a data-entry job's "
                        "documents, following an instruction that describes a single item "
                        "(e.g. \"every container number\", \"each line's HS code\"). You are "
                        "given the instruction, one JSON record per uploaded document, and "
                        "the raw text behind them.\n\n"
                        "Find every occurrence the instruction describes, in the order it "
                        "appears on the document. If nothing matches, return an empty list — "
                        "never invent an item to fill it.\n\n"
                        'Reply with JSON: {"values": [<string>, ...]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Instruction: {prompt}\n\n"
                        + (f"Documents on this job ({len(records)} record(s), all of them):\n"
                           f"{json.dumps(records, ensure_ascii=False)}\n\n" if records else "")
                        + f"Raw document text (supporting evidence, may be shortened):\n"
                        + documents_text[:48000]
                    ),
                },
            ],
        )
        values = json.loads(resp.choices[0].message.content or "{}").get("values", [])
        if not isinstance(values, list):
            return []
        return [str(v).strip() for v in values if str(v).strip()][:CUSTOM_FIELD_ROWS_LIMIT]
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_custom_field_rows failed: %s", exc)
        return []


def compute_custom_field_per_row(prompt: str, documents_text: str, row_context: list[dict],
                                  records: list[dict] | None = None) -> list[str]:
    """Like compute_custom_field_rows, but for a per-row field whose ROWS are already known -
    this job's own product lines, each already carrying whatever OTHER per-row fields (a part
    code, a description, a quantity) extraction already found for it. Returns exactly
    `len(row_context)` values, in the SAME order, one per row - a blank ("") answer for a row
    with nothing found is kept in place, never dropped.

    This is the opposite trade-off from compute_custom_field_rows on purpose.
    compute_custom_field_rows exists for "find every X on the page, however many there are" -
    a container number, a line with no mark of its own - where there is no such thing as a
    known slot count, so a miss simply isn't in the list. A field asked for a value PER
    EXISTING PRODUCT LINE is different: the slot count is already fixed by the job's own real
    line items (see `row_context`), a genuinely blank answer for line 3 is still line 3's
    answer, and dropping it would silently shift every later line's value up by one row - for
    a customs classification code specifically, a value that lands on the wrong product line
    is not a rounding error, it is a wrong customs declaration for the RIGHT product with the
    WRONG code and the WRONG product with none at all.
    """
    settings = get_settings()
    if not row_context:
        return []
    if not settings.openai_api_key:
        return [""] * len(row_context)
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=45)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            temperature=0,
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compute ONE field for EACH of this job's own product lines, "
                        "following an instruction that describes what to look for. You are "
                        "given the instruction, the exact list of product lines to answer for "
                        "(each already carrying whatever this job's own extraction already "
                        "found for it - a part code, a description, a quantity - use these to "
                        "recognise which physical row on the document a line refers to), one "
                        "JSON record per uploaded document, and the raw text behind them.\n\n"
                        "You MUST return exactly one answer per line in row_context, in the "
                        "SAME order, even when a line has nothing to report - use an empty "
                        "string for that line rather than omitting it. The number of answers "
                        "you return must equal the number of lines given, always. Never invent "
                        "a value, never reuse one line's answer for another, and never answer "
                        "from a reference sheet or prior knowledge - only from what a document "
                        "actually, visibly states for that exact line.\n\n"
                        'Reply with JSON: {"values": [<string>, ...]} - the list length equal '
                        "to the number of lines in row_context, in the same order."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Instruction: {prompt}\n\n"
                        f"Product lines to answer for, in order ({len(row_context)} line(s)):\n"
                        f"{json.dumps(row_context, ensure_ascii=False)}\n\n"
                        + (f"Documents on this job ({len(records)} record(s), all of them):\n"
                           f"{json.dumps(records, ensure_ascii=False)}\n\n" if records else "")
                        + f"Raw document text (supporting evidence, may be shortened):\n"
                        + documents_text[:40000]
                    ),
                },
            ],
        )
        values = json.loads(resp.choices[0].message.content or "{}").get("values", [])
        if not isinstance(values, list):
            values = []
        out = [str(v).strip() for v in values]
        # Defensive padding/truncation against a slightly-off count, rather than trust the
        # model's count blindly - a short reply pads with blanks at the END (the tail is
        # where an overlooked last line would fall), a long one is truncated, so a real
        # off-by-one never SHIFTS an earlier line's own correct answer into the wrong slot.
        if len(out) < len(row_context):
            out = out + [""] * (len(row_context) - len(out))
        elif len(out) > len(row_context):
            out = out[: len(row_context)]
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_custom_field_per_row failed: %s", exc)
        return [""] * len(row_context)


def suggest_field_mapping(
    element_label: str,
    element_options: list[str] | None,
    candidate_fields: list[str],
) -> dict:
    """When the Super Admin touches an ERP input, guess which template data field
    belongs in it (and, for a dropdown, which option matches). Returns:
      {"field": <best candidate or null>, "confidence": "high|medium|low",
       "option": <best matching <select> option or null>, "reason": str}
    Degrades to a cheap string-overlap heuristic if OpenAI is unavailable."""
    settings = get_settings()

    def _heuristic() -> dict:
        lab = (element_label or "").lower()
        best, score = None, 0.0
        for f in candidate_fields:
            toks = set(f.lower().replace("_", " ").split())
            if not toks:
                continue
            hit = sum(1 for t in toks if t in lab)
            s = hit / len(toks)
            if s > score:
                best, score = f, s
        opt = None
        if element_options:
            for o in element_options:
                if best and best.replace("_", " ").lower() in o.lower():
                    opt = o
                    break
        return {
            "field": best if score >= 0.5 else None,
            "confidence": "medium" if score >= 0.75 else "low",
            "option": opt,
            "reason": "matched by label word overlap" if best else "no confident match",
        }

    if not settings.openai_api_key or not candidate_fields:
        return _heuristic()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=20)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You map an ERP web form input to one of the available extracted "
                        "document fields. Given the input's on-screen label and the list of "
                        "field names, pick the single best field (or null if none fits). If the "
                        "input is a dropdown, also pick the option that best matches. Reply with "
                        "JSON: {\"field\": <field name or null>, \"confidence\": "
                        "\"high|medium|low\", \"option\": <option text or null>, "
                        "\"reason\": <short>}."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Input label/context: {element_label or '(no label)'}\n"
                        f"Dropdown options: {element_options if element_options else '(not a dropdown)'}\n"
                        f"Available fields: {candidate_fields}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        field = data.get("field")
        if field is not None and field not in candidate_fields:
            field = None  # never invent a field name
        return {
            "field": field,
            "confidence": data.get("confidence") or "low",
            "option": data.get("option"),
            "reason": data.get("reason") or "",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("field-mapping suggestion failed, using heuristic: %s", exc)
        return _heuristic()


def ai_choose_action(goal: str, elements: list[dict], values: dict[str, str] | None = None) -> dict:
    """Pick which on-screen element to click to achieve a goal. Given a natural-language
    goal (e.g. "an 'already logged in — continue?' dialog appeared; continue the flow")
    and the page's clickable elements, return {"index": int|None, "reason": str}. Used for
    the recorder's AI step AND the self-healing takeover when a recorded element is missing.
    Returns index=None when nothing on the page fits the goal (so the caller can skip)."""
    settings = get_settings()

    def _heuristic() -> dict:
        g = (goal or "").lower()
        # crude keyword affinity toward continue/yes/ok/submit vs the goal text
        for i, e in enumerate(elements):
            t = (e.get("text") or "").lower()
            if t and (t in g or g in t):
                return {"index": i, "reason": f"text match: {e.get('text')}"}
        for kw in ("continue", "yes", "ok", "proceed", "confirm", "submit", "next"):
            if kw in g:
                for i, e in enumerate(elements):
                    if kw in (e.get("text") or "").lower():
                        return {"index": i, "reason": f"keyword '{kw}' match"}
        return {"index": None, "reason": "no confident match"}

    if not settings.openai_api_key or not elements:
        return _heuristic()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=20)
        listing = "\n".join(
            f"{i}: <{e.get('tag')}> \"{(e.get('text') or '').strip()}\"" for i, e in enumerate(elements)
        )
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=150,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You drive a web-automation flow. The flow hit a screen and needs to "
                        "decide which element to click to reach the goal. You are given the goal "
                        "and a numbered list of the clickable elements currently on the page. "
                        "Choose the single best element to click. Reply with JSON: "
                        '{"index": <number of the element to click, or null if none fits>, '
                        '"reason": <short>}. Prefer the safe action that continues the flow.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Goal: {goal}\n"
                        + (f"Job data: {json.dumps(values, ensure_ascii=False)}\n" if values else "")
                        + f"Clickable elements:\n{listing}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        idx = data.get("index")
        if isinstance(idx, int) and 0 <= idx < len(elements):
            return {"index": idx, "reason": data.get("reason") or ""}
        return {"index": None, "reason": data.get("reason") or "AI found no matching element"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai_choose_action failed, using heuristic: %s", exc)
        return _heuristic()


def resolve_ai_value(prompt: str, values: dict[str, str], options: list[str] | None = None) -> str:
    """Evaluate an AI rule at ERP-entry time. The Super Admin wrote a natural-language
    condition (e.g. "if the mode of transport is sea put SEA, otherwise AIR"); given the
    job's extracted field values, return the single value to type/select. For a dropdown,
    the answer is constrained to one of `options`."""
    settings = get_settings()
    if not settings.openai_api_key:
        # No AI available — best effort: if the prompt names an option, use it; else blank.
        if options:
            for o in options:
                if o.lower() in (prompt or "").lower():
                    return o
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=20)
        opt_line = (
            f"\nThe field is a dropdown. Your answer MUST be exactly one of these options: {options}."
            if options
            else ""
        )
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=120,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You fill one field of an ERP form by following a rule the operator's "
                        "supervisor wrote. You are given the rule and the document data extracted "
                        "for this job. Apply the rule and return the exact value to enter. "
                        'Reply with JSON: {"value": <string>}. Return only the value itself — no '
                        "labels, units unless the rule asks, or explanation." + opt_line
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Rule: {prompt}\n"
                        f"Extracted job data (field -> value): {json.dumps(values, ensure_ascii=False)}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        val = str(data.get("value", "")).strip()
        if options and val and val not in options:
            # snap to the closest option by case-insensitive containment
            for o in options:
                if o.lower() == val.lower() or val.lower() in o.lower() or o.lower() in val.lower():
                    return o
            # Nothing matched. Returning the answer anyway put a value into a dropdown that
            # does not contain it - the docstring promised the opposite. A blank leaves the
            # field empty, which the value check after the step catches and reports; an
            # off-list string looks like it worked and silently is not there.
            logger.warning(
                "AI rule answered %r, which is not one of the dropdown options %r - "
                "leaving the field blank rather than entering it", val, options,
            )
            return ""
        return val
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI rule resolution failed: %s", exc)
        return ""


def build_field_profile(
    field_name: str,
    anchor_term: str | None,
    value_text: str | None,
    document_type: str,
    correction_prompt: str | None = None,
) -> FieldProfile:
    """Generate the extraction profile for a marked field. `correction_prompt` (from the
    training loop) is folded in as an extra instruction when present."""
    settings = get_settings()
    fallback = _fallback(field_name, anchor_term, value_text)
    if not settings.openai_api_key:
        return fallback

    correction_line = (
        f"\nA human reviewer added this correction guidance: {correction_prompt}"
        if correction_prompt
        else ""
    )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=30)
        resp = create_chat_completion_with_retry(
            client,
            model=settings.openai_model,
            # Pinned: these are transcription tasks, not creative ones. At the API default
            # (1.0) the same document yielded CH20261122 as "CH20231182" and 6/12/2026 as
            # "2026-12-12", and repeat runs scored differently on identical input.
            temperature=0,
            max_tokens=400,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You configure a document-extraction pipeline. Given a field the user "
                        "marked on a sample document, return a JSON object that lets the pipeline "
                        "find that same field on OTHER documents that may caption it differently. "
                        "Keys: "
                        '"anchor_variations" (array of the caption/label strings this field '
                        "commonly appears under across real-world documents of this type — be "
                        "thorough, include abbreviations and synonyms), "
                        '"semantic_description" (one sentence: what the value means), '
                        '"value_format_hint" (the typical shape/format of the value), '
                        '"extraction_prompt" (one instruction telling an LLM how to locate and '
                        "return ONLY the raw value, matching by meaning when no known caption "
                        "appears). Reply with the JSON object only."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Document type: {document_type}\n"
                        f"Field name: {field_name}\n"
                        f"Caption I marked it next to: {anchor_term or '(none detected)'}\n"
                        f"Example value I marked: {value_text or '(unreadable)'}"
                        f"{correction_line}"
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        variations = data.get("anchor_variations") or fallback.anchor_variations
        if isinstance(variations, str):
            variations = [variations]
        # Always keep the actually-observed caption in the list.
        if anchor_term and anchor_term not in variations:
            variations = [anchor_term, *variations]
        return FieldProfile(
            anchor_variations=[str(v) for v in variations],
            semantic_description=data.get("semantic_description") or fallback.semantic_description,
            value_format_hint=data.get("value_format_hint") or fallback.value_format_hint,
            extraction_prompt=data.get("extraction_prompt") or fallback.extraction_prompt,
        )
    except Exception as exc:  # noqa: BLE001 — any API/parse failure degrades to fallback
        logger.warning("OpenAI profile generation failed, using fallback: %s", exc)
        return fallback

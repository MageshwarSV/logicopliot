"""Auto ERP Submission: choosing a master value when nobody is there to pick it.

On the job screen an operator picks the ERP Importer Name from the Importer master, then one of
its branches. In auto mode (app/core/auto_erp.py) the system picks instead:

  * Importer name - the Document Importer Name (the "Consignee" read off the documents) matched
    against the master: first an exact match once company words are normalised ("Pvt. Ltd." =
    "PRIVATE LIMITED"), preferring the job's importer country; when several names are close the
    AI chooses among the closest few; nothing close = left blank.
  * Quotation name - the Document Quotation Name (the forwarder read off the documents) matched
    the same way against the customer's quotation list; a forwarder's overseas office (e.g.
    "Expeditors International (UK) BFS") may be billed through its Indian company.
  * A branch (importer or quotation) - the only one, else the one naming the job's branch
    (Bangalore), else the AI chooses using the consignee address on the document, else the first.
  * Junk rows ("." and the like) are never chosen.
"""
from __future__ import annotations

import difflib
import json
import logging
import re

logger = logging.getLogger(__name__)

_WORDS = {
    "PVT": "PRIVATE", "PRIV": "PRIVATE", "PTE": "PRIVATE", "LTD": "LIMITED", "LIMITE": "LIMITED",
    "CO": "COMPANY", "COMP": "COMPANY", "CORP": "CORPORATION", "INTL": "INTERNATIONAL",
    "INT": "INTERNATIONAL", "INC": "INCORPORATED", "MFG": "MANUFACTURING", "TECH": "TECHNOLOGIES",
    "SOLN": "SOLUTION", "SOLUTIONS": "SOLUTION", "TECHNOLOGY": "TECHNOLOGIES", "&": "AND",
}
_DROP = {"THE", "M/S", "MS", "MESSRS"}


def is_junk(value: str | None) -> bool:
    """"." / "-" / "N/A"-style rows that exist in the masters but name nothing."""
    v = (value or "").strip()
    return not re.search(r"[A-Za-z0-9]{2,}", v) or v.upper() in {"NA", "N/A", "NIL", "NONE", "TBA"}


def norm_company(name: str | None) -> str:
    s = (name or "").upper().replace("&", " & ")
    s = re.sub(r"[^A-Z0-9& ]+", " ", s)
    words = [_WORDS.get(w, w) for w in s.split() if w not in _DROP]
    return " ".join(words)


def _score(a: str, b: str) -> float:
    """How alike two normalised names are: character similarity, pulled down when the words
    differ (JUNIPER NETWORKS INC vs JUNIPER NETWORKS SOLUTION INDIA)."""
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    words = len(wa & wb) / max(len(wa | wb), 1)
    return 0.6 * ratio + 0.4 * words


def ai_choose(question: str, candidates: list[str], tenant_id: str | None) -> int | None:
    """Ask the AI to pick one candidate. Returns its index, or None when it says none fits or
    the AI cannot be reached (the caller then falls back)."""
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    listed = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    prompt = (f"{question}\n\nOptions:\n{listed}\n\n"
              'Return JSON {"choice": <option number, or 0 if none of them is right>, "reason": "short"}')
    try:
        resp = create_chat_completion_with_retry(
            timeout=30, tenant_id=tenant_id, model=get_settings().openai_model, max_tokens=150,
            temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        n = int(json.loads(resp.choices[0].message.content or "{}").get("choice") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto ERP: AI choice failed - %s", exc)
        return None
    return n - 1 if 1 <= n <= len(candidates) else None


def match_name(doc_name: str | None, options: list[dict], country: str | None = None,
               tenant_id: str | None = None, *, question: str | None = None) -> tuple[str | None, str]:
    """(master value, how it was chosen) for a company name read off the documents.
    options: [{"value", "match_values": [name, country, ...]}] as the reference list gives them."""
    target = norm_company(doc_name)
    if not target:
        return None, ""
    want_country = (country or "").strip().upper()
    rows = [(o["value"], ((o.get("match_values") or [None, None])[1] or "").strip().upper())
            for o in options if o.get("value") and not is_junk(o["value"])]

    def by_country(vals):
        same = [v for v, c in vals if want_country and c == want_country]
        return same[0] if same else vals[0][0]

    exact = [(v, c) for v, c in rows if norm_company(v) == target]
    if exact:
        return by_country(exact), "exact match"

    first = target.split()[0]
    scored: dict[str, float] = {}
    for v, c in rows:
        n = norm_company(v)
        if first not in n.split():
            continue
        s = _score(target, n) + (0.05 if want_country and c == want_country else 0)
        scored[v] = max(scored.get(v, 0), s)
    top = sorted(scored.items(), key=lambda kv: -kv[1])[:6]
    top = [(v, s) for v, s in top if s >= 0.5]
    if not top:
        return None, ""
    if top[0][1] >= 0.92 and (len(top) == 1 or top[0][1] - top[1][1] >= 0.08):
        return top[0][0], "close match"
    pick = ai_choose(
        question.format(doc_name=doc_name) if question else (
            f'A customs document names the importer "{doc_name}"'
            + (f" (country {want_country})" if want_country else "")
            + ". Which of these entries in the ERP's importer list is the SAME company? "
              "Different countries or different legal entities are not the same company."),
        [v for v, _ in top], tenant_id)
    if pick is not None:
        return top[pick][0], "AI match"
    return None, ""


def pick_branch(options: list[str], *, job_branch: str | None = None, address: str | None = None,
                company: str | None = None, tenant_id: str | None = None) -> tuple[str | None, str]:
    """(branch, how) among a master entry's branches."""
    opts = [o for o in dict.fromkeys(options) if o and not is_junk(o)]
    if not opts:
        return None, ""
    if len(opts) == 1:
        return opts[0], "only branch"
    jb = (job_branch or "").strip().upper()
    if jb:
        named = [o for o in opts if jb in o.upper()]
        if named:
            return named[0], f"names the job branch {job_branch}"
    if (address or "").strip():
        pick = ai_choose(
            f'The importer{f" {company}" if company else ""} has these branches in the ERP. The '
            f'consignee address on the shipment documents is:\n"{address.strip()}"\n'
            f'{f"The job is handled at the {job_branch} branch. " if job_branch else ""}'
            "Which branch is this address (same city or state)?", opts, tenant_id)
        if pick is not None:
            return opts[pick], "AI from the consignee address"
    return opts[0], "first branch"


QUOTATION_QUESTION = (
    'The shipment documents name the freight forwarder "{doc_name}". The options are the '
    "customs broker's own customers (the companies it bills). Which option is this forwarder? "
    "A forwarder's office abroad (UK, US, ...) counts as its Indian company in the list, but a "
    "different forwarder group never does - answer 0 if none is the same group."
)

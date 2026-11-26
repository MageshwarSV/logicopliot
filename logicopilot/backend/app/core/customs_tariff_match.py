"""Nearest CTH from the Indian Customs Tariff (ICEGATE) - phase 2, the matching engine.

Used only where the customer's own Reference CTH Dump has nothing usable for a line (that logic,
in material_master.py, is unchanged). Every answer is an ACTIVE code of the global tariff - never
anything else.

For each line (document CTH, part code, description):

  1. Document CTH is an active tariff code          -> use it as it is, no AI.  how="document"
  2. Answered before for this tenant, same wording  -> the remembered code.     how="ai"
  3. AI. The tariff file has no descriptions (2026-11-26), so it is two steps:
       a. A short list of tariff codes: the codes under the document CTH's first 6 digits
          (or 4). A line with no usable document code first asks the AI for its 8-digit
          Indian tariff item from the description; an exact active hit is the answer,
          otherwise its first 6 (or 4) digits give the list.
       b. The AI picks ONE code from that list (each shown with its unit and duty rate).
     The answer is checked against the list; one outside it is never used.          how="ai"
  4. The AI failed or answered outside the list -> the subheading's residual "Other" code
     (ending 90 / 99 / 00), so Auto ERP always has an allowed code.             how="fallback"
  5. Nothing to go on (no code, no part, no words) -> "".

One AI call per step for a whole batch of lines (40 at a time), not one per line.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.customs_tariff import active_codes, clean_cth
from app.models.customs_tariff import CthTariffSuggestion, CustomsTariff

logger = logging.getLogger(__name__)

BATCH = 40
MAX_CHOICES = 150          # codes shown for one line; a 4-digit heading rarely has more
_CONFIDENCE = ("high", "medium", "low")


@dataclass
class Line:
    key: Any
    doc_cth: str = ""
    part_code: str = ""
    description: str = ""


@dataclass
class Suggestion:
    cth: str = ""
    how: str = ""              # "document" | "ai" | "fallback" | ""
    confidence: str = ""       # AI's own: high / medium / low
    note: str = ""


def _norm(text: str) -> str:
    return " ".join(str(text or "").upper().split())


def line_key(line: Line) -> str:
    raw = "|".join((re.sub(r"\D", "", line.doc_cth or ""), _norm(line.part_code), _norm(line.description)))
    return hashlib.sha256(raw.encode()).hexdigest()


def _choices(codes: frozenset[str], prefix: str) -> list[str]:
    return sorted(c for c in codes if c.startswith(prefix)) if prefix else []


def candidates_for(codes: frozenset[str], digits: str) -> tuple[str, list[str]]:
    """(prefix used, the active codes under it): 6 digits, then 4. ("", []) when neither."""
    digits = re.sub(r"\D", "", digits or "")
    for n in (6, 4):
        if len(digits) >= n:
            found = _choices(codes, digits[:n])
            if found:
                return digits[:n], found[:MAX_CHOICES]
    return "", []


def residual(choices: list[str], hint: str = "") -> str:
    """The subheading's "Other" code: the one ending 90 / 99 / 00 that shares most digits
    with the hint, else the last code of the list (the tariff lists "Other" last)."""
    if not choices:
        return ""
    hint = re.sub(r"\D", "", hint or "")

    def shared(c: str) -> int:
        n = 0
        while n < min(len(c), len(hint)) and c[n] == hint[n]:
            n += 1
        return n

    for ending in ("90", "99", "00"):
        others = [c for c in choices if c.endswith(ending)]
        if others:
            return max(others, key=lambda c: (shared(c), c))
    return choices[-1]


# ---------------------------------------------------------------- AI

def _ask(system: str, user: str, tenant_id: str | None, n_lines: int) -> dict[int, dict]:
    from app.core.config import get_settings
    from app.core.llm import create_chat_completion_with_retry

    settings = get_settings()
    if not settings.openai_api_key:
        return {}
    try:
        resp = create_chat_completion_with_retry(
            timeout=90, tenant_id=tenant_id, model=settings.openai_model, temperature=0,
            max_tokens=60 + 40 * n_lines, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 - an AI failure falls back, never breaks the job
        logger.warning("customs tariff AI call failed: %s", exc)
        return {}
    out: dict[int, dict] = {}
    for item in data.get("lines") or []:
        try:
            out[int(item.get("n"))] = item
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _describe(n: int, line: Line) -> str:
    parts = [f"Line {n}:"]
    if (line.doc_cth or "").strip():
        parts.append(f"code printed on the document: {line.doc_cth.strip()}")
    if (line.part_code or "").strip():
        parts.append(f"part code: {line.part_code.strip()}")
    parts.append(f"description: {(line.description or '').strip() or '(none)'}")
    return " | ".join(parts)


_GUESS_SYSTEM = (
    "You are an Indian customs classification expert. For each product line, give the 8-digit "
    "Indian Customs Tariff item (CTH / ITC-HS, First Schedule of the Customs Tariff Act, 1975) "
    "it is most likely classified under, from its description and part code. A code printed on "
    "the document may be a foreign HS / HTS code: its first 6 digits are international and "
    "usually right, the last digits are national. Answer for EVERY line; leave cth empty only "
    "when the line carries no product information at all.\n\n"
    'Reply with JSON: {"lines": [{"n": <line number>, "cth": "<8 digits>"}]}'
)

_PICK_SYSTEM = (
    "You are an Indian customs classification expert. Each product line comes with a list of "
    "valid 8-digit Indian Customs Tariff items (CTH), each shown with its unit and basic duty "
    "rate. For each line choose the ONE item from ITS OWN list whose official tariff "
    "description fits the product best. When no specific item fits, choose the residual "
    "'Other' item of the subheading (usually ending in 90). Never answer a code that is not in "
    "that line's list. Say how sure you are: high, medium or low.\n\n"
    'Reply with JSON: {"lines": [{"n": <line number>, "cth": "<8 digits>", "confidence": "high|medium|low"}]}'
)


def _guess(lines: list[Line], tenant_id: str | None) -> dict[int, str]:
    out: dict[int, str] = {}
    for start in range(0, len(lines), BATCH):
        chunk = lines[start:start + BATCH]
        user = "\n".join(_describe(start + i + 1, ln) for i, ln in enumerate(chunk))
        for n, item in _ask(_GUESS_SYSTEM, user, tenant_id, len(chunk)).items():
            out[n] = re.sub(r"\D", "", str(item.get("cth") or ""))
    return out


def _pick(lines: list[Line], choices: list[list[str]], info: dict[str, CustomsTariff],
          tenant_id: str | None) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for start in range(0, len(lines), BATCH):
        blocks = []
        for i, (ln, codes) in enumerate(zip(lines[start:start + BATCH], choices[start:start + BATCH])):
            listing = "; ".join(
                f"{c} ({info[c].unit or '-'}, {info[c].rate or '-'})"
                + (f" {info[c].description}" if info[c].description else "")
                for c in codes if c in info
            )
            blocks.append(f"{_describe(start + i + 1, ln)}\n  Choices: {listing}")
        size = len(blocks)
        for n, item in _ask(_PICK_SYSTEM, "\n".join(blocks), tenant_id, size).items():
            conf = str(item.get("confidence") or "").lower()
            out[n] = (re.sub(r"\D", "", str(item.get("cth") or "")), conf if conf in _CONFIDENCE else "")
    return out


# ---------------------------------------------------------------- the engine

def suggest_many(db: Session, lines: list[Line], tenant_id: str | None,
                 remember: bool = True) -> dict[Any, Suggestion]:
    """The nearest allowed CTH for each line, keyed by Line.key. See the module docstring."""
    codes = active_codes(db)
    out: dict[Any, Suggestion] = {ln.key: Suggestion() for ln in lines}
    if not codes:
        for ln in lines:
            out[ln.key].note = "The Indian Customs Tariff has not been uploaded yet."
        return out

    todo: list[Line] = []
    keys: dict[Any, str] = {}
    for ln in lines:
        doc = clean_cth(ln.doc_cth)
        if doc and doc in codes:
            out[ln.key] = Suggestion(doc, "document", "high", "Document CTH is in the tariff.")
            continue
        if not ((ln.doc_cth or "").strip() or (ln.part_code or "").strip() or (ln.description or "").strip()):
            out[ln.key].note = "No document CTH, part code or description to go on."
            continue
        keys[ln.key] = line_key(ln)
        todo.append(ln)

    if todo:
        known = {
            s.line_key: s for s in db.query(CthTariffSuggestion).filter(
                CthTariffSuggestion.tenant_id == tenant_id,
                CthTariffSuggestion.line_key.in_(list(set(keys.values()))),
            )
        }
        still = []
        for ln in todo:
            hit = known.get(keys[ln.key])
            if hit and hit.cth in codes:
                out[ln.key] = Suggestion(hit.cth, "ai", hit.confidence or "", "Remembered from an earlier line with the same wording.")
            else:
                still.append(ln)
        todo = still

    # Step a: each line's short list.
    shortlist: dict[Any, list[str]] = {}
    hints: dict[Any, str] = {}
    need_guess = []
    for ln in todo:
        _prefix, found = candidates_for(codes, ln.doc_cth)
        if found:
            shortlist[ln.key], hints[ln.key] = found, re.sub(r"\D", "", ln.doc_cth or "")
        else:
            need_guess.append(ln)
    if need_guess:
        guesses = _guess(need_guess, tenant_id)
        for i, ln in enumerate(need_guess, start=1):
            g = guesses.get(i, "")
            # Even a guess that is itself an allowed code goes through step b with its
            # neighbours, so a plausible-but-wrong last two digits can still be corrected.
            _prefix, found = candidates_for(codes, g)
            if found:
                shortlist[ln.key], hints[ln.key] = found, g
            else:
                out[ln.key].note = "The AI could not place this line in the tariff."

    # Step b: pick one code from each line's list.
    picking = [ln for ln in todo if ln.key in shortlist]
    if picking:
        lists = [shortlist[ln.key] for ln in picking]
        wanted = {c for lst in lists for c in lst}
        info = {t.cth: t for t in db.query(CustomsTariff).filter(CustomsTariff.cth.in_(list(wanted)))}
        picks = _pick(picking, lists, info, tenant_id)
        for i, ln in enumerate(picking, start=1):
            code, conf = picks.get(i, ("", ""))
            if code in shortlist[ln.key]:
                out[ln.key] = Suggestion(code, "ai", conf, "AI's nearest match in the Indian Customs Tariff.")
                if remember:
                    db.add(CthTariffSuggestion(
                        tenant_id=tenant_id, line_key=keys[ln.key], doc_cth=(ln.doc_cth or "")[:40] or None,
                        part_code=(ln.part_code or "")[:255] or None, description=ln.description or None,
                        cth=code, confidence=conf or None,
                    ))
            else:
                code = residual(shortlist[ln.key], hints.get(ln.key, ""))
                out[ln.key] = Suggestion(code, "fallback", "low",
                                         "The AI gave no usable answer - the subheading's 'Other' code was used.")
        if remember:
            db.commit()
    return out


def suggest_one(db: Session, doc_cth: str = "", part_code: str = "", description: str = "",
                tenant_id: str | None = None, remember: bool = True) -> Suggestion:
    return suggest_many(db, [Line(0, doc_cth, part_code, description)], tenant_id, remember)[0]


# ---------------------------------------------------------------- the customers' dumps

def dump_check(db: Session) -> list[dict]:
    """Every template with a CTH dump: how many of its codes are NOT in the tariff. Read-only.
    A dump still being read in the background shows ready=False (look again shortly)."""
    from app.core import cth_master_rows as rows_mod
    from app.core import material_master as mm
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    codes = active_codes(db)
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    out = []
    for g in db.query(TemplateGroup).all():
        if not mm.master_path(g.id).exists():
            continue
        item = {"group_id": g.id, "template": g.name, "tenant": tenants.get(g.tenant_id, ""), "ready": False}
        parsed = rows_mod.cached(g.id)
        if parsed is not None:
            rows = rows_mod.view(parsed)
            filled = [r for r in rows if r["cth"]]
            missing_rows = [r for r in filled if r["cth"] not in codes]
            distinct = {r["cth"] for r in filled}
            missing = sorted({r["cth"] for r in missing_rows})
            item.update(
                ready=True, rows=len(rows), rows_with_cth=len(filled), distinct_codes=len(distinct),
                codes_not_in_tariff=len(missing), rows_not_in_tariff=len(missing_rows),
                examples=[{"cth": r["cth_as_written"] or r["cth"], "part_code": r["part_code"],
                           "description": r["description"][:120]} for r in missing_rows[:25]],
            )
        out.append(item)
    out.sort(key=lambda x: (x["tenant"].lower(), x["template"].lower()))
    return out

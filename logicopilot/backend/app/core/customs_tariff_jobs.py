"""Indian Customs Tariff on jobs - phase 3 (human review). Only for a template with
TemplateGroup.use_customs_tariff on AND a tariff uploaded; everything else is untouched.

The customer's Reference CTH Dump lookup runs first, exactly as before (jobs.run_extraction).
Then, for the template's CTH lookup fields ("Dump CTH Number", "CTH"):

- a value that is an active tariff code stays as it is (it came from the dump);
- a value NOT in the tariff (an old or wrong dump code, "NA", a previous entry) is not used:
  emptied, with a note saying so;
- an empty line gets the nearest tariff code from customs_tariff_match (document CTH in the
  tariff -> as is; otherwise the AI), marked value_source = tariff_doc / tariff_ai /
  tariff_fallback. It goes in the ANSWER box ("CTH"), not in "Dump CTH Number" - that box only
  ever shows what the customer's dump really gave (4S, 2026-11-26). The screen labels the CTH
  box "AI suggestion from the Indian Customs Tariff - not from the dump"; the operator may
  change it, and it goes to the ERP as it is.

A code typed or picked must be in the tariff - check_correction(); submit_problems() blocks
only a CTH outside the tariff.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.customs_tariff import active_codes, clean_cth
from app.core.tariff_codes import is_tariff_code_field

logger = logging.getLogger(__name__)

SUGGESTED = ("tariff_doc", "tariff_ai", "tariff_fallback")
_SOURCE = {"document": "tariff_doc", "ai": "tariff_ai", "fallback": "tariff_fallback"}


def tariff_on(db: Session, group) -> bool:
    return bool(group is not None and getattr(group, "use_customs_tariff", False) and active_codes(db))


def tariff_fields(db: Session, group_id: str) -> list:
    """The template's per-row CTH lookup fields - the boxes the dump fills."""
    from app.models.custom_field import CustomField

    return [
        c for c in db.query(CustomField).filter(CustomField.group_id == group_id,
                                                CustomField.per_row.is_(True),
                                                CustomField.kind == "lookup").all()
        if not getattr(c, "skip_material_master", False) and is_tariff_code_field(c.label_name)
    ]


def is_tariff_field(db: Session, cf) -> bool:
    return bool(cf is not None and cf.per_row and (cf.kind or "") == "lookup"
                and not getattr(cf, "skip_material_master", False) and is_tariff_code_field(cf.label_name))


def apply_to_job(db: Session, job, group) -> dict:
    """After extraction: keep tariff codes, drop codes outside it, suggest for empty lines.
    Does not commit. Never raises - a failure leaves the dump's values as they were."""
    try:
        return _apply(db, job, group)
    except Exception:  # noqa: BLE001
        logger.exception("customs tariff pass failed on job %s - values left as read", job.reference)
        return {}


def _apply(db: Session, job, group) -> dict:
    from app.core.customs_tariff_match import Line, suggest_many
    from app.models.job import JobFieldValue

    if not tariff_on(db, group):
        return {}
    fields = tariff_fields(db, group.id)
    if not fields:
        return {}
    codes = active_codes(db)
    field_ids = {c.id for c in fields}
    # The answer box a picker feeds ("CTH", fed by "Dump CTH Number") gets no suggestion of its
    # own: the operator's tick writes it. With one there, the tick would show as already made.
    fed = {sid for c in fields for sid in (c.sync_field_ids or []) if sid != c.id}
    doc_ids = {c.paired_custom_field_id for c in fields if c.paired_custom_field_id}
    key_labels = {c.id: (c.lookup_key_label or "").strip() for c in fields}

    rows = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id,
                                          JobFieldValue.row_index.isnot(None)).all()
    text: dict[tuple, dict[str, str]] = {}
    doc_cth: dict[tuple, str] = {}
    for fv in rows:
        k = (fv.set_index or 1, fv.row_index)
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if val and not text.setdefault(k, {}).get(fv.label_name):
            text[k][fv.label_name] = val
        if fv.custom_field_id in doc_ids and val and not doc_cth.get(k):
            doc_cth[k] = val

    need: dict[tuple, Line] = {}
    targets: list = []
    stats = {"kept": 0, "rejected": 0, "suggested": 0}
    # Per line, the empty boxes: "answer" (CTH, the one the picker feeds) and "dump".
    empty: dict[tuple, dict[str, object]] = {}
    for fv in rows:
        if fv.custom_field_id not in field_ids:
            continue
        k = (fv.set_index or 1, fv.row_index)
        val = (fv.extracted_value or "").strip()
        fv.value_source, fv.source_note = None, None
        if val:
            code = clean_cth(val)
            if code and code in codes:
                if code != val:
                    fv.extracted_value = code
                stats["kept"] += 1
                continue
            fv.extracted_value = ""
            fv.source_note = f"Dump code {val} is not in the Indian Customs Tariff."
            stats["rejected"] += 1
        empty.setdefault(k, {}).setdefault("answer" if fv.custom_field_id in fed else "dump", fv)

    for k, boxes in empty.items():
        dump_fv = boxes.get("dump")
        if dump_fv is None:
            continue            # the dump gave this line a tariff code - nothing to suggest
        # The suggestion goes in the CTH answer box; a template without one keeps it in the dump box.
        target = boxes.get("answer") or dump_fv
        t = text.get(k, {})
        label = key_labels.get(dump_fv.custom_field_id) or ""
        part = (t.get(label) if label else "") or t.get("item_material_code", "")
        need.setdefault(k, Line(k, doc_cth.get(k, ""), part, t.get("product_description", "")))
        targets.append(target)

    if need:
        out = suggest_many(db, list(need.values()), job.tenant_id)
        for fv in targets:
            s = out.get((fv.set_index or 1, fv.row_index))
            if s is None or not s.cth:
                if s is not None and s.note:
                    fv.source_note = " ".join(filter(None, [fv.source_note, s.note]))
                continue
            fv.extracted_value = s.cth
            fv.value_source = _SOURCE.get(s.how)
            fv.source_note = " ".join(filter(None, [fv.source_note, s.note]))
            stats["suggested"] += 1
    logger.info("job %s: customs tariff - %s kept, %s dump code(s) not in the tariff, %s suggested",
                job.reference, stats["kept"], stats["rejected"], stats["suggested"])
    return stats


def suggestion_needs_confirmation(fv) -> bool:
    """A tariff suggestion nobody has confirmed yet. Since 2026-11-26 the suggestion sits in the
    CTH answer box and goes as it is, so nothing blocks on this any more; kept for callers."""
    return (fv.value_source or "") in SUGGESTED and not (fv.corrected_value or "").strip()


def check_correction(db: Session, job, cf, saved: str) -> str:
    """The value to store for an operator's CTH, or raises ValueError with the reason.
    Only on a tariff field of a template with the tariff on; anything else passes unchanged."""
    if not (saved or "").strip() or cf is None or not is_tariff_field(db, cf):
        return saved
    from app.models.template_group import TemplateGroup

    if not tariff_on(db, db.get(TemplateGroup, job.group_id)):
        return saved
    code = clean_cth(saved)
    if not code:
        raise ValueError(f"{saved.strip()} is not an 8-digit CTH.")
    if code not in active_codes(db):
        raise ValueError(f"{code} is not in the Indian Customs Tariff - pick a code from the list.")
    return code


def submit_problems(db: Session, job) -> list[str]:
    """Submit check, per line: any CTH answer that is not in the tariff. A tariff suggestion is
    already in the CTH box (labelled as one on screen), so it does not need a tick first."""
    from app.models.job import JobFieldValue
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id)
    if not tariff_on(db, group):
        return []
    fields = {c.id: c.label_name for c in tariff_fields(db, group.id)}
    if not fields:
        return []
    codes = active_codes(db)
    fvs = db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id,
                                         JobFieldValue.custom_field_id.in_(list(fields)),
                                         JobFieldValue.row_index.isnot(None)
                                         ).order_by(JobFieldValue.set_index, JobFieldValue.row_index).all()
    out = []
    for fv in fvs:
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if val and clean_cth(val) not in codes:
            out.append(f"{fields[fv.custom_field_id]} (line {fv.row_index}): {val} is not in the Indian Customs Tariff")
    return out


# ---------------------------------------------------------------- Auto ERP (phase 4)

def tariff_labels(db: Session, job) -> set[str]:
    """Labels of the job's CTH fields when its template uses the tariff, else empty."""
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id)
    if not tariff_on(db, group):
        return set()
    return {c.label_name for c in tariff_fields(db, group.id)}


def _where(keys) -> str:
    keys = sorted(keys)
    multi = len({s for s, _r in keys}) > 1
    parts = [f"invoice {s} line {r}" if multi else str(r) for s, r in keys]
    return ("lines " if len(parts) > 1 else "line ") + ", ".join(parts) if not multi else ", ".join(parts)


def auto_apply(db: Session, job) -> list[str]:
    """Auto ERP Submission: the tariff code on each line goes to the ERP without a person's
    tick. The after-extraction pass already put it in the Dump CTH box; here it is copied into
    the answer box the picker would have filled ("CTH"), as the extracted value - never as a
    correction. Nothing outside the tariff is ever written. Returns the job-note lines.
    Does not commit."""
    from app.models.job import JobFieldValue
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id)
    if not tariff_on(db, group):
        return []
    fields = tariff_fields(db, group.id)
    if not fields:
        return []
    codes = active_codes(db)
    fed = {sid for c in fields for sid in (c.sync_field_ids or []) if sid != c.id}
    ids = {c.id for c in fields}
    by_line: dict[tuple, list] = {}
    for fv in db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id,
                                             JobFieldValue.custom_field_id.in_(list(ids)),
                                             JobFieldValue.row_index.isnot(None)).all():
        by_line.setdefault((fv.set_index or 1, fv.row_index), []).append(fv)

    seen: dict[str, set] = {"tariff_ai": set(), "tariff_fallback": set(), "tariff_doc": set(),
                            "rejected": set(), "none": set()}
    for k, fvs in by_line.items():
        code, source = "", None
        # The box the operator would tick first (Dump CTH Number), then any other CTH box.
        for fv in sorted(fvs, key=lambda f: f.custom_field_id in fed):
            val = clean_cth(fv.corrected_value or fv.extracted_value or "")
            if val and val in codes:
                code, source = val, fv.value_source
                break
        if any("is not in the Indian Customs Tariff" in (fv.source_note or "") for fv in fvs):
            seen["rejected"].add(k)
        if not code:
            seen["none"].add(k)
            continue
        if source in seen:
            seen[source].add(k)
        for fv in fvs:
            if fv.custom_field_id in fed and (fv.corrected_value or "").strip() == "":
                if clean_cth(fv.extracted_value or "") not in codes:
                    fv.extracted_value = code
                    fv.value_source = source
    db.flush()

    notes = []
    picked = []
    if seen["tariff_ai"]:
        picked.append(f"AI's nearest tariff code on {_where(seen['tariff_ai'])}")
    if seen["tariff_doc"]:
        picked.append(f"document CTH (in the tariff) on {_where(seen['tariff_doc'])}")
    if seen["tariff_fallback"]:
        picked.append(f"the subheading's 'Other' code on {_where(seen['tariff_fallback'])}")
    if picked:
        notes.append("CTH from the Indian Customs Tariff: " + "; ".join(picked) + " - check in the ERP")
    if seen["rejected"]:
        notes.append(f"Dump CTH not in the Indian Customs Tariff, not used, on {_where(seen['rejected'])}")
    if seen["none"]:
        notes.append(f"No tariff CTH could be found on {_where(seen['none'])} - fill it in the ERP")
    return notes


def erp_value(db: Session, raw, codes: frozenset | None = None) -> str:
    """What may go to the ERP in a CTH column: the tariff code, or "" - never anything else."""
    code = clean_cth(raw)
    return code if code and code in (codes if codes is not None else active_codes(db)) else ""

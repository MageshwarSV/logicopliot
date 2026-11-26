"""Indian Customs Tariff on jobs - phase 3 (human review). Only for a template with
TemplateGroup.use_customs_tariff on AND a tariff uploaded; everything else is untouched.

The customer's Reference CTH Dump lookup runs first, exactly as before (jobs.run_extraction).
Then, for the template's CTH lookup fields ("Dump CTH Number", "CTH"):

- a value that is an active tariff code stays as it is (it came from the dump);
- a value NOT in the tariff (an old or wrong dump code, "NA", a previous entry) is not used:
  emptied, with a note saying so;
- an empty line gets the nearest tariff code from customs_tariff_match (document CTH in the
  tariff -> as is; otherwise the AI), marked value_source = tariff_doc / tariff_ai /
  tariff_fallback.

A suggestion is not an answer: the operator confirms it (a tick in the picker, or typing a code)
before the job can be submitted - submit_problems(). A code typed or picked must be in the
tariff - check_correction().
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
        if fv.custom_field_id in fed:
            continue
        t = text.get(k, {})
        label = key_labels.get(fv.custom_field_id) or ""
        part = (t.get(label) if label else "") or t.get("item_material_code", "")
        need.setdefault(k, Line(k, doc_cth.get(k, ""), part, t.get("product_description", "")))
        targets.append(fv)

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
    """A tariff suggestion nobody has confirmed yet - not an answer for the submit check."""
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
    """Submit check, per line: a tariff suggestion nobody confirmed (no CTH box on that line
    has an operator's value - a tick in the picker or a typed code saves one), and any CTH
    answer that is not in the tariff."""
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
    confirmed = {(fv.set_index or 1, fv.row_index) for fv in fvs if (fv.corrected_value or "").strip()}
    out, asked = [], set()
    for fv in fvs:
        k = (fv.set_index or 1, fv.row_index)
        if suggestion_needs_confirmation(fv) and k not in confirmed and k not in asked:
            asked.add(k)
            out.append(f"CTH (line {fv.row_index}): confirm the suggested tariff code {fv.extracted_value}")
        val = (fv.corrected_value or fv.extracted_value or "").strip()
        if val and clean_cth(val) not in codes:
            out.append(f"{fields[fv.custom_field_id]} (line {fv.row_index}): {val} is not in the Indian Customs Tariff")
    return out

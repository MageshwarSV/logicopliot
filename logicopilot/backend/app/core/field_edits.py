"""Operator edits of extracted fields: recorded as they happen, read back for Super Admin ->
Extraction Corrections.

record() is called where an operator's own edit is saved (jobs.correct_field_value and
jobs.carry_across_rows) - not for the values that flow on automatically to a synced partner
box, which would count one edit twice.

The screen answers two questions:
  summary()  which fields are corrected on many DIFFERENT jobs - a recurring extraction error,
             the signal for improving that field's prompt (default: 3 or more jobs)
  edits()    for one field, every job where it was corrected: what the AI read, what the
             operator saved, how many times, and the history of each edit

Older corrections, made before field_value_edits existed, are still seen: a field whose saved
value differs from what the AI read (job_field_values.corrected_value vs extracted_value)
counts as corrected, with its number of edits unknown ("1+").
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_EDIT_ROWS = 500


def _clean(v) -> str:
    return " ".join(str(v or "").split())


def kind_of(ai_value, final_value) -> str:
    """wrong - the AI read a value and it was changed; missed - the AI read nothing and the
    operator filled it; cleared - the operator emptied what the AI read."""
    ai, final = _clean(ai_value), _clean(final_value)
    if not ai:
        return "missed"
    if not final:
        return "cleared"
    return "wrong"


def record(db: Session, fv, before, after, user=None, source: str = "edit") -> None:
    """Add one edit row (not committed - it goes with the caller's own commit). Nothing is
    written when the value did not actually change. Never raises: recording an edit must not
    stop the edit itself."""
    try:
        if _clean(before) == _clean(after):
            return
        from app.models.field_value_edit import FieldValueEdit
        from app.models.job import Job

        job = db.get(Job, fv.job_id)
        db.add(FieldValueEdit(
            tenant_id=fv.tenant_id, job_id=fv.job_id,
            job_reference=(getattr(job, "reference", None) or fv.job_id)[:255],
            group_id=getattr(job, "group_id", None),
            template_document_id=fv.template_document_id, job_document_id=fv.job_document_id,
            mark_id=fv.mark_id, custom_field_id=fv.custom_field_id, field_value_id=fv.id,
            label_name=(fv.label_name or "")[:100], row_index=fv.row_index, set_index=fv.set_index,
            ai_value=fv.extracted_value, before_value=before, after_value=after,
            source=source,
            edited_by_id=getattr(user, "id", None),
            edited_by_name=(getattr(user, "full_name", None) or getattr(user, "email", None) or "")[:255] or None,
        ))
    except Exception:  # noqa: BLE001
        logger.warning("could not record a field edit", exc_info=True)


def _names(db: Session, group_ids: set, tdoc_ids: set, tenant_ids: set) -> tuple[dict, dict, dict]:
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    groups = {g.id: g.name for g in db.query(TemplateGroup.id, TemplateGroup.name)
              .filter(TemplateGroup.id.in_(group_ids or {""})).all()}
    tdocs = {d.id: d.name for d in db.query(TemplateDocument.id, TemplateDocument.name)
             .filter(TemplateDocument.id.in_(tdoc_ids or {""})).all()}
    tenants = {t.id: t.name for t in db.query(Tenant.id, Tenant.name)
               .filter(Tenant.id.in_(tenant_ids or {""})).all()}
    return groups, tdocs, tenants


def _current_corrections(db: Session, tenant_id=None, group_id=None, since: datetime | None = None,
                         field: tuple | None = None):
    """Live field values whose saved value differs from what the AI read."""
    from app.models.job import Job, JobFieldValue

    q = (db.query(JobFieldValue.id, JobFieldValue.job_id, JobFieldValue.tenant_id, Job.group_id,
                  JobFieldValue.custom_field_id,
                  Job.reference, JobFieldValue.template_document_id, JobFieldValue.job_document_id,
                  JobFieldValue.label_name, JobFieldValue.row_index, JobFieldValue.set_index,
                  JobFieldValue.extracted_value, JobFieldValue.corrected_value,
                  JobFieldValue.updated_at)
         .join(Job, Job.id == JobFieldValue.job_id)
         .filter(JobFieldValue.corrected_value.isnot(None),
                 func.trim(JobFieldValue.corrected_value)
                 != func.trim(func.coalesce(JobFieldValue.extracted_value, ""))))
    if tenant_id:
        q = q.filter(JobFieldValue.tenant_id == tenant_id)
    if group_id:
        q = q.filter(Job.group_id == group_id)
    if since:
        q = q.filter(JobFieldValue.updated_at >= since)
    if field:
        g, td, label = field
        q = q.filter(Job.group_id == g, JobFieldValue.label_name == label,
                     JobFieldValue.template_document_id.is_(None) if not td
                     else JobFieldValue.template_document_id == td)
    return q.all()


def _logged_edits(db: Session, tenant_id=None, group_id=None, since: datetime | None = None,
                  field: tuple | None = None):
    from app.models.field_value_edit import FieldValueEdit as E

    q = db.query(E)
    if tenant_id:
        q = q.filter(E.tenant_id == tenant_id)
    if group_id:
        q = q.filter(E.group_id == group_id)
    if since:
        q = q.filter(E.created_at >= since)
    if field:
        g, td, label = field
        q = q.filter(E.group_id == g, E.label_name == label,
                     E.template_document_id.is_(None) if not td else E.template_document_id == td)
    return q.order_by(E.created_at).all()


def _jobs_with_field(db: Session, keys: set) -> dict:
    """How many live jobs hold each field at all - the base for the correction rate."""
    from app.models.job import Job, JobFieldValue

    if not keys:
        return {}
    labels = {k[2] for k in keys}
    rows = (db.query(Job.group_id, JobFieldValue.template_document_id, JobFieldValue.label_name,
                     func.count(func.distinct(JobFieldValue.job_id)))
            .join(Job, Job.id == JobFieldValue.job_id)
            .filter(JobFieldValue.label_name.in_(labels),
                    Job.group_id.in_({k[0] for k in keys}))
            .group_by(Job.group_id, JobFieldValue.template_document_id, JobFieldValue.label_name)
            .all())
    return {(g, td, label): n for g, td, label, n in rows if (g, td, label) in keys}


def _operator_field_ids(db: Session, rows) -> set:
    """Custom fields of kind "hardcoded": filled by the operator (branch, consignee, insurance)
    or set to a fixed value - never read by the AI, so a correction there is not an extraction
    error. Left out unless asked for."""
    from app.models.custom_field import CustomField

    ids = {r.custom_field_id for r in rows if r.custom_field_id}
    if not ids:
        return set()
    return {i for (i,) in db.query(CustomField.id).filter(CustomField.id.in_(ids),
                                                          CustomField.kind == "hardcoded").all()}


def _ai_only(db: Session, current, logged, include_operator_fields: bool):
    if include_operator_fields:
        return current, logged
    skip = _operator_field_ids(db, list(current) + list(logged))
    return ([r for r in current if r.custom_field_id not in skip],
            [e for e in logged if e.custom_field_id not in skip])


def summary(db: Session, *, min_jobs: int = 3, tenant_id=None, group_id=None,
            since: datetime | None = None, include_operator_fields: bool = False) -> dict:
    current = _current_corrections(db, tenant_id, group_id, since)
    logged = _logged_edits(db, tenant_id, group_id, since)
    current, logged = _ai_only(db, current, logged, include_operator_fields)

    # One entry per field (template, document, field name); inside it, one per job line.
    fields: dict[tuple, dict] = defaultdict(lambda: {"jobs": set(), "edits": 0, "lines": {},
                                                      "last": None, "tenant": None, "kinds": defaultdict(int)})
    for e in logged:
        k = (e.group_id, e.template_document_id, e.label_name)
        f = fields[k]
        f["jobs"].add(e.job_id)
        f["edits"] += 1
        f["tenant"] = f["tenant"] or e.tenant_id
        line = (e.job_id, e.row_index, e.set_index, e.job_document_id)
        f["lines"][line] = {"ai": e.ai_value, "final": e.after_value, "logged": True,
                            "job_reference": e.job_reference}
        f["last"] = max(filter(None, [f["last"], e.created_at]))
    for r in current:
        k = (r.group_id, r.template_document_id, r.label_name)
        f = fields[k]
        f["jobs"].add(r.job_id)
        f["tenant"] = f["tenant"] or r.tenant_id
        line = (r.job_id, r.row_index, r.set_index, r.job_document_id)
        if line in f["lines"]:
            f["lines"][line]["final"] = r.corrected_value     # the value as it stands now
        else:
            f["edits"] += 1                                   # corrected before edits were logged
            f["lines"][line] = {"ai": r.extracted_value, "final": r.corrected_value,
                                "logged": False, "job_reference": r.reference}
        f["last"] = max(filter(None, [f["last"], r.updated_at]))

    keep = {k: f for k, f in fields.items() if len(f["jobs"]) >= max(1, min_jobs)}
    groups, tdocs, tenants = _names(db, {k[0] for k in keep if k[0]}, {k[1] for k in keep if k[1]},
                                    {f["tenant"] for f in keep.values() if f["tenant"]})
    base = _jobs_with_field(db, set(keep))
    out = []
    for (g, td, label), f in keep.items():
        for line in f["lines"].values():
            f["kinds"][kind_of(line["ai"], line["final"])] += 1
        examples = [{"job_reference": v["job_reference"], "ai_value": v["ai"], "final_value": v["final"]}
                    for v in list(f["lines"].values())[-3:]]
        with_field = base.get((g, td, label)) or 0
        out.append({
            "group_id": g, "template_document_id": td, "label_name": label,
            "template": groups.get(g) or "(deleted template)",
            "document": tdocs.get(td) if td else "Computed field",
            "tenant": tenants.get(f["tenant"]) or "",
            "jobs_corrected": len(f["jobs"]), "total_edits": f["edits"],
            "lines_corrected": len(f["lines"]),
            "jobs_with_field": with_field,
            "correction_rate": round(len(f["jobs"]) / with_field * 100, 1) if with_field else None,
            "wrong": f["kinds"]["wrong"], "missed": f["kinds"]["missed"],
            "cleared": f["kinds"]["cleared"],
            "last_seen": f["last"].isoformat() if f["last"] else None,
            "examples": examples,
        })
    out.sort(key=lambda r: (-r["jobs_corrected"], -r["total_edits"], r["label_name"]))
    from app.models.field_value_edit import FieldValueEdit

    first = db.query(func.min(FieldValueEdit.created_at)).scalar()
    return {"fields": out, "history_since": first.isoformat() if first else None,
            "min_jobs": min_jobs}


def edits(db: Session, *, group_id: str, label_name: str, template_document_id: str | None = None,
          tenant_id=None, since: datetime | None = None) -> dict:
    from app.models.job import Job

    field = (group_id, template_document_id or None, label_name)
    logged = _logged_edits(db, tenant_id, None, since, field)
    current = _current_corrections(db, tenant_id, None, since, field)

    lines: dict[tuple, dict] = {}
    for e in logged:
        key = (e.job_id, e.row_index, e.set_index, e.job_document_id)
        line = lines.setdefault(key, {
            "job_id": e.job_id, "job_reference": e.job_reference, "job_document_id": e.job_document_id,
            "row_index": e.row_index, "set_index": e.set_index, "ai_value": e.ai_value,
            "final_value": e.after_value, "history": [], "last_at": None})
        line["history"].append({"before": e.before_value, "after": e.after_value,
                                "by": e.edited_by_name, "at": e.created_at.isoformat(),
                                "source": e.source})
        line["final_value"] = e.after_value
        line["last_at"] = e.created_at
    for r in current:
        key = (r.job_id, r.row_index, r.set_index, r.job_document_id)
        line = lines.get(key)
        if line is None:
            lines[key] = {"job_id": r.job_id, "job_reference": r.reference,
                          "job_document_id": r.job_document_id, "row_index": r.row_index,
                          "set_index": r.set_index, "ai_value": r.extracted_value,
                          "final_value": r.corrected_value, "history": [], "last_at": r.updated_at}
        else:
            line["final_value"] = r.corrected_value
    live = {j for (j,) in db.query(Job.id).filter(Job.id.in_({k[0] for k in lines} or {""})).all()}
    out = []
    for line in lines.values():
        n = len(line["history"])
        out.append({**line, "edit_count": n or None,          # None = corrected before logging began
                    "kind": kind_of(line["ai_value"], line["final_value"]),
                    "job_exists": line["job_id"] in live,
                    "last_at": line["last_at"].isoformat() if line["last_at"] else None})
    out.sort(key=lambda r: r["last_at"] or "", reverse=True)
    jobs = {r["job_id"] for r in out}
    return {"lines": out[:MAX_EDIT_ROWS], "total_lines": len(out), "jobs": len(jobs)}

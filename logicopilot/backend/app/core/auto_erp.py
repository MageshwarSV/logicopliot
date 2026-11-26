"""Auto ERP Submission - a job goes to the ERP the moment extraction finishes, with no review in
Cargora. The customer's own team corrects anything in the ERP afterwards (asked for by 4S
Logistics, Sea Import Bangalore).

Who switches it on:
  * SaaS Admin: Tenant.auto_erp_allowed - off means the tenant cannot use it at all.
  * Tenant Admin (or SaaS Admin): Tenant.auto_erp_rules, per template, optionally only for
    some branches (the value of the ERP script's branch field, as the branch logins use it).
Everything defaults OFF: a tenant or template nobody configured keeps the manual review flow.

What the run is: exactly the submission GK1's Final Submit runs when GK2 is off for the mode
(jobs._kick_off_real_erp_submission - the same queue, branch logins, failure banner and retry).
Agreed rules for the auto path (2026-11-25):
  * cross-document rule failures do not stop it - it is submitted anyway;
  * a required operator-pick field still empty gets a value: the importer name matched from
    the Document Importer Name, a branch chosen by the job's branch or the consignee address,
    else the first option (never a junk row) - see fill_operator_defaults and
    app/core/auto_erp_match.py; the customer changes it in the ERP if it is wrong; a field with
    nothing to choose from stays blank and the job still goes;
  * BUSINESS RULE: with Auto ERP Submission on, nothing in Cargora blocks the job - no review,
    no validation, no approval. Wrong or incomplete values go as they are and 4S corrects them
    in the ERP. The billing customer (ERP Quotation Name) and the importer are matched from the
    documents, else the first option; a missing document does not stop it either;
  * the only things it will not do: send a job flagged as a possible duplicate (that would be a
    second ERP entry), send the same job twice, or send when the template has no ready ERP
    script / no Excel entry (nothing could reach the ERP - noted in Job.auto_erp_note);
  * Verified stays a manual step, as for every other submitted job.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LEVEL_ORDER = {"name": 0, "branch": 1, "address": 2}


# --------------------------------------------------------------------------------------- config

def rule_for(tenant, group_id: str | None) -> dict | None:
    """This template's rule when Auto ERP Submission is allowed AND switched on for it."""
    if tenant is None or not getattr(tenant, "auto_erp_allowed", False) or not group_id:
        return None
    rule = (tenant.auto_erp_rules or {}).get(group_id)
    if not isinstance(rule, dict) or not rule.get("enabled"):
        return None
    return rule


def clean_branches(branches) -> list[str]:
    out: list[str] = []
    for b in branches or []:
        b = str(b or "").strip()
        if b and b.casefold() not in {x.casefold() for x in out}:
            out.append(b)
    return out


def branch_matches(rule: dict, branch: str | None) -> bool:
    """No branches listed = every branch. Otherwise the job's branch must be one of them."""
    wanted = clean_branches(rule.get("branches"))
    if not wanted:
        return True
    return (branch or "").strip().casefold() in {b.casefold() for b in wanted}


def ready_script(db: Session, job):
    from app.models.erp_script import ErpScript

    return next((s for s in db.query(ErpScript).filter(
        ErpScript.tenant_id == job.tenant_id, ErpScript.status == "ready").all()
        if job.group_id in (s.template_ids or [])), None)


# ----------------------------------------------------------------------- operator-pick defaults

def _first_option(cf) -> str | None:
    for o in cf.fixed_options or []:
        v = o.get("value") or o.get("label") if isinstance(o, dict) else o
        v = str(v or "").strip()
        # "H:H - Home" is code:label - the job saves and the ERP reads the code (see
        # parseFixedOption in JobRunPage.tsx).
        if ":" in v:
            v = v.split(":", 1)[0].strip()
        if v:
            return v
    return None


_DOC_NAME_LABELS = ("Consignee", "consignee_full_name", "Document Importer Name", "consignee_name")
_DOC_ADDRESS_LABELS = ("Consignee Address",)
_COUNTRY_LABELS = ("ERP Consignee Country",)
_DOC_QUOTATION_LABELS = ("Quotation Name", "Document Quotation Name")


def fill_operator_defaults(db: Session, job, branch: str | None = None) -> list[str]:
    """Give every required operator-pick field that is still empty (or holds only a junk value
    like ".") a value, the way an operator would pick it (app/core/auto_erp_match.py):
      * Importer name: the Document Importer Name matched against the Importer master;
      * a master's branch: among the picked name's own branches - the one naming the job's
        branch, else the AI from the consignee address, else the first;
      * anything else: the first option its list or fixed choices offer.
    Written as the field's extracted value, never as a correction, so the screen still shows it
    was not a person's choice. Returns "Label = value (how)" for each one filled."""
    from app.api.v1.jobs import (ERP_ENTRY_LABEL, _quotation_master_names, _quotation_name_key,
                                 _reference_options_payload)
    from app.core.auto_erp_match import QUOTATION_QUESTION, is_junk, match_name, pick_branch
    from app.core.erp_ui_fields import master_of
    from app.core.importer_gstin import gstin_rule
    from app.models.custom_field import CustomField
    from app.models.job import JobFieldValue
    from app.models.template_group import TemplateGroup

    grp = db.get(TemplateGroup, job.group_id)
    fields = db.query(CustomField).filter(
        CustomField.group_id == job.group_id,
        CustomField.ask_operator.is_(True),
        CustomField.ask_operator_required.is_(True),
    ).all()
    fields = [c for c in fields if not getattr(c, "per_row", False) and getattr(c, "kind", "") != "lookup"]
    # A master's name before its branch: the branch is picked among that name's own branches.
    fields.sort(key=lambda c: _LEVEL_ORDER.get((master_of(c.label_name) or {}).get("level"), 3))

    current: dict[str, JobFieldValue] = {
        fv.label_name: fv for fv in db.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.row_index.is_(None)).all()}

    def val(labels) -> str:
        for label in labels:
            fv = current.get(label)
            if fv is not None and (fv.value or "").strip():
                return fv.value.strip()
        return ""

    country = val(_COUNTRY_LABELS)
    filled: list[str] = []
    for cf in fields:
        fv = current.get(cf.label_name)
        if fv is not None and (fv.value or "").strip():
            # A person's choice, or a real reading, stays. Only a junk reading is replaced.
            # Only a reading with no letter or digit at all (".", "-") counts as junk here: a
            # one-letter code like "H" or "P" is a real value.
            if fv.corrected_value is not None or re.search(r"[A-Za-z0-9]", fv.value):
                continue
        value, how = None, ""
        master = master_of(cf.label_name) or {}
        parent = master.get("depends_on")
        gst = gstin_rule(db, grp, cf) if grp is not None else None
        if parent:
            pfv = current.get(parent)
            key = (pfv.value or "").strip() if pfv is not None else ""
            # No name picked = no way to choose among its branches; leave it.
            opts = (_reference_options_payload(db, cf.id, key=key, country=country, gstin=gst)["options"]
                    if key else [])
            if opts:
                importer = master.get("source") == "importer_master"
                value, how = pick_branch(
                    [str(o["value"]).strip() for o in opts], job_branch=branch,
                    address=val(_DOC_ADDRESS_LABELS) if importer else None, company=key,
                    tenant_id=job.tenant_id)
        else:
            opts = _reference_options_payload(db, cf.id, country=country, gstin=gst)["options"]
            if (cf.label_name or "").strip().lower() == ERP_ENTRY_LABEL:
                names = _quotation_master_names(db, job.group_id)
                if names is not None:
                    opts = [o for o in opts if _quotation_name_key(o.get("value")) in names]
                # The billing customer: matched from the forwarder on the documents first; only
                # when nothing matches does it fall back to the first customer (below) - the
                # job must still reach the ERP, where 4S corrects it.
                doc_q = val(_DOC_QUOTATION_LABELS)
                value, how = match_name(doc_q, opts, None, job.tenant_id, question=QUOTATION_QUESTION)
                if value:
                    how = f'{how} for "{doc_q}"'
            if master.get("source") == "importer_master" and master.get("level") == "name":
                doc_name = val(_DOC_NAME_LABELS)
                value, how = match_name(doc_name, opts, country, job.tenant_id)
                if value:
                    how = f'{how} for "{doc_name}"'
            if opts and not value:
                value = next((str(o["value"]).strip() for o in opts if not is_junk(o.get("value"))), None)
                how = ("no match on the documents - first option, check it in the ERP"
                       if master.get("level") == "name" else "first option") if value else ""
        if not value:
            value = _first_option(cf)
            how = "first choice" if value else ""
        if not value:
            continue
        if fv is None:
            fv = JobFieldValue(tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cf.id,
                               label_name=cf.label_name, extracted_value=value)
            db.add(fv)
            current[cf.label_name] = fv
        else:
            fv.extracted_value = value
        filled.append(f"{cf.label_name} = {value} ({how})" if how else f"{cf.label_name} = {value}")
    db.flush()
    return filled


# ------------------------------------------------------------------------------------- the hook

def _hold(db: Session, job, reason: str) -> None:
    job.auto_erp_note = f"Auto ERP submission held for manual review: {reason}"
    db.commit()
    logger.info("job %s: auto ERP submission held - %s", job.reference, reason)


def maybe_auto_submit(db: Session, job) -> bool:
    """Called when a job becomes ready for review (extraction finished, or a possible duplicate
    was approved). Sends it to the ERP when its tenant/template/branch has Auto ERP Submission
    on; otherwise leaves it exactly as it is. True when it was sent."""
    from app.api.v1.jobs import _kick_off_real_erp_submission, _missing_mandatory_documents, entry_values_and_rows
    from app.models.job_event import JobEvent
    from app.models.template_group import TemplateGroup
    from app.models.tenant import Tenant

    if job.status != "extracted" or job.gk2_status is not None:
        return False
    # Never twice: a re-extraction of a job already sent stays with the people.
    if job.auto_erp_submitted or job.ai_submitted_at is not None or job.erp_submitted_at is not None:
        return False
    rule = rule_for(db.get(Tenant, job.tenant_id), job.group_id)
    if rule is None:
        return False

    # Business rule (2026-11-25): with Auto ERP Submission on, nothing in Cargora blocks the
    # job - a missing document included. It goes with what was read; 4S corrects it in the ERP.
    missing = _missing_mandatory_documents(db, job)
    grp = db.get(TemplateGroup, job.group_id)
    script = ready_script(db, job)
    if script is None:
        _hold(db, job, "no ready ERP script is linked to this template.")
        return False
    if grp is None or (grp.entry_mode or "fields") != "excel":
        _hold(db, job, "this template is not set up for Excel entry, so nothing would reach the ERP.")
        return False

    branch_field = (script.branch_field or "").strip()
    branch = ""
    if branch_field:
        values, _rows = entry_values_and_rows(db, job)
        branch = str(values.get(branch_field) or "").strip()
    if not branch_matches(rule, branch):
        return False            # another branch: the normal manual review

    filled = fill_operator_defaults(db, job, branch)
    # Every dropdown field on the job (header and product lines) gets a value too, so the screen
    # shows what goes to the ERP - Total Quantity Unit, each line's unit, ...
    from app.core.auto_erp_complete import fill_job_values

    filled += fill_job_values(db, job)
    # Indian Customs Tariff (template switch on): each line's tariff code goes without a tick.
    from app.core.customs_tariff_jobs import auto_apply as _tariff_auto

    tariff_notes = _tariff_auto(db, job)
    job.auto_erp_submitted = True
    job.gk2_skipped = True      # no GK2 review - and lets a failed run be retried (gk2_approve)
    job.erp_submitted_by_id = None
    note = "Sent to the ERP automatically after extraction (Auto ERP Submission)."
    if branch:
        note += f" Branch: {branch}."
    if missing:
        note += " Sent without: " + ", ".join(missing) + " (not received)."
    if filled:
        note += " Picked automatically: " + "; ".join(filled) + "."
    if tariff_notes:
        note += " " + ". ".join(tariff_notes) + "."
    job.auto_erp_note = note
    db.add(JobEvent(tenant_id=job.tenant_id, job_id=job.id, status="extracted",
                    stage="ERP Entry", note=note[:2000]))
    db.commit()
    logger.info("job %s: auto ERP submission%s", job.reference, f" ({branch})" if branch else "")
    _kick_off_real_erp_submission(db, job)
    return True


def safe_maybe_auto_submit(db: Session, job) -> None:
    """maybe_auto_submit that can never break the caller: a crash here leaves the job in Ready
    for Review, as if the feature were off, with the reason noted."""
    try:
        maybe_auto_submit(db, job)
    except Exception as exc:  # noqa: BLE001
        logger.exception("job %s: auto ERP submission crashed", getattr(job, "reference", job.id))
        db.rollback()
        try:
            job.auto_erp_note = f"Auto ERP submission could not start: {exc}"[:2000]
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

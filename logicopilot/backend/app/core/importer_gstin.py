"""An Indian importer is offered on a job only once its GSTIN is in the dump.

The importer on a job is picked through three dump tables, in a cascade:

    ERP Consignee Name                  (name, country)          -> ERP importer name
    ERP Importer Address / Branch Code  (ERP importer name)      -> branch
    ERP Branch Address                  (name, branch, country)  -> address   [+ gstin]

The GSTIN lives on the ERP Branch Address row (CustomFieldReferenceValue.gstin) - one per
importer BRANCH, because a GSTIN is issued per state registration, not per company.

With TemplateGroup.importer_requires_gstin on, for country INDIA:
  - a branch is offered only when its own address row has a GSTIN;
  - an importer name is offered only when at least one of its Indian branches has one;
  - its address is offered only for a branch that has one.
The moment a GSTIN is typed onto a branch in the dump, that branch - and its importer - is
offered again. Nothing is deleted and nothing is switched off: the row is simply not listed
until it is complete.

Every other country is untouched: a foreign company has no Indian GSTIN to wait for.

Only the lists a person picks from are narrowed. A value already saved on a job is left as it
is, and the automatic match made while a document is read is not changed by this.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.reference_cache import normalize_key
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref

NAME_LABEL = "erp consignee name"
BRANCH_LABEL = "erp importer address / branch code"
ADDRESS_LABEL = "erp branch address"
GSTIN_COUNTRY = "INDIA"


def gstin_rule(db: Session, group, field) -> dict | None:
    """What this field is in the importer cascade, when the rule is on for its template.

    {"role": "name" | "branch" | "address", "address_field_id": ...} - or None when the rule
    is off, the field is not one of the three, or the template has no branch-address table to
    read a GSTIN from (then there is nothing to decide by, and nothing is hidden)."""
    if group is None or field is None or not getattr(group, "importer_requires_gstin", False):
        return None
    label = (field.label_name or "").strip().lower()
    role = {NAME_LABEL: "name", BRANCH_LABEL: "branch", ADDRESS_LABEL: "address"}.get(label)
    if role is None:
        return None
    if role == "address":
        return {"role": role, "address_field_id": field.id}
    address = (
        db.query(CustomField.id)
        .filter(CustomField.group_id == group.id, func.lower(func.trim(CustomField.label_name)) == ADDRESS_LABEL)
        .first()
    )
    if address is None:
        return None
    return {"role": role, "address_field_id": address[0]}


def _has_gstin():
    return func.trim(func.coalesce(Ref.gstin, "")) != ""


def names_with_gstin(db: Session, address_field_id: str):
    """Subquery: every Indian importer name with at least one active branch that has a GSTIN."""
    return (
        db.query(Ref.match_value_1)
        .filter(Ref.custom_field_id == address_field_id, Ref.match_value_3 == GSTIN_COUNTRY,
                Ref.is_active.is_(True), _has_gstin())
        .distinct()
    )


def branches_with_gstin(db: Session, address_field_id: str, name: str) -> set[str]:
    """The Indian branches of one importer that have a GSTIN, as normalised branch keys."""
    rows = (
        db.query(Ref.match_value_2)
        .filter(Ref.custom_field_id == address_field_id, Ref.match_value_1 == normalize_key(name),
                Ref.match_value_3 == GSTIN_COUNTRY, Ref.is_active.is_(True), _has_gstin())
        .all()
    )
    return {r[0] for r in rows if r[0]}


def is_gstin_country(country: str | None) -> bool:
    return normalize_key(country or "") == GSTIN_COUNTRY


COUNTRY_LABEL = "erp consignee country"


def clear_importer_without_gstin(db: Session, job) -> list[str]:
    """A job whose saved INDIAN importer has no GSTIN in the dump is shown with the importer
    empty - so it cannot go on to the ERP with an importer that is not offered any more.
    Returns the labels it emptied. Does not commit.

    Called when a job is opened and when its documents have just been read, so it reaches both
    a job already in progress when the rule was switched on and a job whose importer the
    reader matched by itself (the lists are narrowed in jobs.py; this is the same rule for a
    value that is already there).

      - the importer has no Indian branch with a GSTIN  -> name, branch and address emptied
      - the importer has one, but not the branch picked  -> branch and address emptied

    "Emptied" is a correction to blank, written the way an operator's own would be: every
    screen, the Excel and the ERP entry read it as empty, and what the documents originally
    read stays in the record underneath. It is not put back when the GSTIN is added later -
    the importer is then in the list again and is picked like any other.

    A completed job is never touched: it is the record of what was filed.
    """
    from app.models.job import JobFieldValue
    from app.models.template_group import TemplateGroup

    if job is None or job.status == "completed":
        return []
    group = db.get(TemplateGroup, job.group_id)
    if group is None or not getattr(group, "importer_requires_gstin", False):
        return []
    wanted = {NAME_LABEL: "name", BRANCH_LABEL: "branch", ADDRESS_LABEL: "address", COUNTRY_LABEL: "country"}
    field_ids: dict[str, str] = {}
    for cf in db.query(CustomField).filter(CustomField.group_id == group.id).all():
        role = wanted.get((cf.label_name or "").strip().lower())
        if role and role not in field_ids:
            field_ids[role] = cf.id
    if not {"name", "address", "country"} <= set(field_ids):
        return []
    by_role: dict[str, JobFieldValue] = {}
    by_id = {v: k for k, v in field_ids.items()}
    for fv in (db.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id, JobFieldValue.row_index.is_(None),
                       JobFieldValue.custom_field_id.in_(list(field_ids.values()))).all()):
        by_role.setdefault(by_id[fv.custom_field_id], fv)

    def value(role: str) -> str:
        fv = by_role.get(role)
        if fv is None:
            return ""
        return (fv.corrected_value if fv.corrected_value is not None else fv.extracted_value or "").strip()

    if not is_gstin_country(value("country")) or not value("name"):
        return []
    allowed = branches_with_gstin(db, field_ids["address"], value("name"))
    if not allowed:
        doomed = ("name", "branch", "address")
    elif value("branch") and normalize_key(value("branch")) not in allowed:
        doomed = ("branch", "address")
    else:
        return []
    emptied: list[str] = []
    for role in doomed:
        fv = by_role.get(role)
        if fv is not None and value(role):
            fv.corrected_value = ""
            emptied.append(fv.label_name)
    return emptied

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

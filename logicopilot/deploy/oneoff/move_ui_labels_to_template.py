"""One-off (2026-11-28): move the job screen's HARDCODED field labels into each template.

The job screen (frontend/src/pages/jobs/JobRunPage.tsx) names a field by:
    the template's own display label  ->  else a hardcoded list in the code  ->  else its internal name.
This copies, for every field that today gets its name from the hardcoded lists, exactly that
name into the template's own display label (FieldMark / CustomField.display_label). The screen
already prefers the template label, so nothing an operator sees changes; afterwards the
hardcoded lists can be removed from the code (step B) without any name changing.

What it never touches:
  - a field that already has its own display label (kept as it is)
  - a field with an AI-aided rule (its label is used in the AI prompt) - skipped and listed
  - the internal field name (label_name) that the Excel, ERP, rules and robot work on

Safe by default:
  - dry run unless --apply; prints every field it would set, template by template
  - checks, for every field, that the name on screen before == after; stops otherwise
  - writes a JSON backup of every value it changes before writing
  - --restore <backup.json> puts the old values back

Run on the server's backend folder (SIT first):
  PYTHONPATH=. venv/bin/python ../deploy/oneoff/move_ui_labels_to_template.py              (dry run)
  PYTHONPATH=. venv/bin/python ../deploy/oneoff/move_ui_labels_to_template.py --apply      (write)
  PYTHONPATH=. venv/bin/python ../deploy/oneoff/move_ui_labels_to_template.py --restore /root/ui-labels-backup-....json
"""
import json
import sys
from datetime import datetime, timezone

import app.main  # noqa: F401 - loads every model
from app.db.session import SessionLocal
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant

# ---- copied VERBATIM from JobRunPage.tsx (FIELD_LABEL_OVERRIDES_BY_DOC_TYPE / _BY_GROUP /
# ADDITIONAL_DETAILS_LABEL_OVERRIDES), 2026-11-28
BY_DOC = {
    "BL": {
        "Supplier Address": "Shipper Address", "Supplier Name": "Shipper Name", "HBL No": "HBL Number",
        "vessel_name": "Vessel Name", "voyage_number": "Voyage Number", "Consignment Type": "Shipment Type",
        "port_of_entry": "Port of Entry", "CountryOfShipmentCode": "Shipment Country Code",
        "TransportModeCode": "Transport Mode Code", "package_count": "Package Count", "Gross Wt": "Gross Weight",
        "container_number": "Container Number", "container_type": "Container Type",
        "ContainerTypeCode": "CTR ISO CODE", "Destination": "ERP Branch", "CustomsHouseCode": "Customs House Code",
    },
    "Invoice": {
        "Invoice No": "Invoice Number", "Currency_type": "Currency", "Product Value": "Total Invoice Value",
        "total_quantity": "Total Quantity", "unit": "Quantity Unit", "shipper": "Shipper Name",
        "Consignee": "Document Importer Name", "ERP Consignee Name": "ERP Importer Name",
        "ERP Consignee Country": "ERP Importer Country",
        "ERP Importer Address / Branch Code": "ERP Importer Branch",
        "CountryOfOriginCode": "Country of Origin Code", "TOI": "Terms of Invoice",
        "product_description": "Product Description", "item_material_code": "Item Material Code",
        "item_quantity": "Item Quantity", "item_unit_price": "Unit Price", "item_po_no": "Purchase Order Number",
        "product_amount": "Product Amount", "product quantity type": "Product Quantity Unit",
        "Customer Part code": "Customer Part Code", "RITC No.": "CTH",
        "Combined Description": "Combined Product Description",
    },
    "PackingList": {"package_count": "Package Count", "total_quantity": "Total Quantity"},
    "Custom": {
        "insurance_percentage": "Insurance Rate (%)", "insurance_amount": "Insurance Amount",
        "Frt_Currency": "Freight Currency", "Misc_Charge_Currency": "Misc Charge Currency",
        "Ins_Currency": "Insurance Currency",
    },
}
BY_GROUP = {"9b7a9384-4030-4052-ad5c-b33da7bf2912": {"HBL No": "HAWB Number"}}  # Air import (November 5)
ADDITIONAL = {
    "Quotation Name": "Document Quotation Name", "Erp Entry Name": "ERP Quotation Name",
    "branch_name": "ERP Quotation Branch", "Payment Method": "Duty Payment Method",
    "AdvancePriorNormal": "Bill of Entry Type", "BETypeCode": "BE Type", "IGM No": "IGM Number",
    "MBL No": "Master Bill of Lading Number", "MBL Date": "Master Bill of Lading Date",
    "insurance_percentage": "Insurance Rate (%)", "insurance_amount": "Insurance Amount",
    "Frt_Currency": "Freight Currency", "Misc_Charge_Currency": "Misc Charge Currency",
    "Ins_Currency": "Insurance Currency",
}


def code_label(group_id, doc_type, label):
    """The name the job screen's code gives a document field (fieldLabelOverride)."""
    if BY_GROUP.get(group_id, {}).get(label) is not None:
        return BY_GROUP[group_id][label]
    return BY_DOC.get(doc_type or "", {}).get(label)


def restore(path):
    data = json.load(open(path))
    db = SessionLocal()
    n = 0
    for row in data["changed"]:
        model = FieldMark if row["table"] == "field_marks" else CustomField
        obj = db.get(model, row["id"])
        if obj is not None:
            obj.display_label = row["old"]
            n += 1
    db.commit()
    db.close()
    print(f"restored {n} display label(s) from {path}")


if "--restore" in sys.argv:
    restore(sys.argv[sys.argv.index("--restore") + 1])
    sys.exit(0)

APPLY = "--apply" in sys.argv
db = SessionLocal()
tenants = {t.id: t.name for t in db.query(Tenant).all()}
changed, skipped_ai, problems = [], [], []
total_kept = 0
try:
    for g in db.query(TemplateGroup).order_by(TemplateGroup.name).all():
        docs = {d.id: d for d in db.query(TemplateDocument).filter(TemplateDocument.group_id == g.id).all()}
        plan = []          # (obj, table, where, label_name, new_label)
        kept = 0
        for m in (db.query(FieldMark).filter(FieldMark.document_id.in_(list(docs))).all() if docs else []):
            new = code_label(g.id, docs[m.document_id].doc_type, m.label_name)
            if not new:
                continue
            if (m.display_label or "").strip():
                kept += 1
                continue
            if (getattr(m, "ai_aided_prompt", None) or "").strip():
                skipped_ai.append(f"{g.name}: {m.label_name}")
                continue
            plan.append((m, "field_marks", docs[m.document_id].name, m.label_name, new))
        for c in db.query(CustomField).filter(CustomField.group_id == g.id).all():
            d = docs.get(c.display_document_id) if c.display_document_id else None
            # On a document tab the screen uses that document's list; otherwise Additional details.
            if d is not None:
                new, where = code_label(g.id, d.doc_type, c.label_name), d.name
            else:
                new, where = ADDITIONAL.get(c.label_name), "Additional details"
            # Old jobs read under "RITC No." show "CTH" from the code list; the CTH field gets
            # its own label so they keep showing "CTH" once that list is gone.
            if not new and c.label_name == "CTH":
                new, where = "CTH", (d.name if d is not None else "Additional details")
            if not new:
                continue
            if (c.display_label or "").strip():
                kept += 1
                continue
            if (getattr(c, "ai_aided_prompt", None) or "").strip():
                skipped_ai.append(f"{g.name}: {c.label_name}")
                continue
            plan.append((c, "custom_fields", where, c.label_name, new))

        # The name on screen before (code list) and after (template label) must be the same.
        for obj, table, where, ln, new in plan:
            if table == "field_marks":
                before = code_label(g.id, docs[obj.document_id].doc_type, ln)
            elif obj.display_document_id and docs.get(obj.display_document_id) is not None:
                before = code_label(g.id, docs[obj.display_document_id].doc_type, ln) or (ln if ln == "CTH" else None)
            else:
                before = ADDITIONAL.get(ln) or (ln if ln == "CTH" else None)
            if before != new:
                problems.append(f"{g.name}: {ln} would change on screen from {before!r} to {new!r}")

        total_kept += kept
        if not plan and not kept:
            continue
        print(f"\n== {tenants.get(g.tenant_id, '?')} / {g.name}: set {len(plan)} | already labelled, kept {kept}")
        for obj, table, where, ln, new in plan:
            print(f"     [{where[:20]:20}] {ln[:36]:36} -> {new}")
            changed.append({"table": table, "id": obj.id, "group": g.name, "label_name": ln,
                            "old": obj.display_label, "new": new})
            obj._pending_label = new

    print(f"\nTOTAL: set {len(changed)} | kept {total_kept} | skipped (AI rule) {len(skipped_ai)} {skipped_ai}")
    if problems:
        print("\nSTOP - these would change what the operator sees:")
        for p in problems:
            print("  ", p)
        sys.exit(1)
    if not APPLY:
        print("\nDry run only - nothing written. Run again with --apply to write.")
        sys.exit(0)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"/root/ui-labels-backup-{stamp}.json"
    with open(path, "w") as fh:
        json.dump({"taken_at": stamp, "changed": changed}, fh, indent=1)
    print(f"backup written: {path}")
    for row in changed:
        model = FieldMark if row["table"] == "field_marks" else CustomField
        obj = db.get(model, row["id"])
        obj.display_label = row["new"]
    db.commit()
    print(f"applied: {len(changed)} display label(s) written. Undo with --restore {path}")
finally:
    db.close()

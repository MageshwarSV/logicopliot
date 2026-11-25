"""Screen Designer preview before a template has any job: the onboarding data stands in.

A new customer's screen is designed before the first job exists, so the designer shows each
field with the value onboarding already holds for it:

  1. the value read inside its Step 2 box on this document's sample, else
  2. its value in the customer's ERP Excel (Step 1, first row), else
  3. its fixed value (Step 4).

An onboarding field (an ERP Excel column) is linked to the template field it fills through the
template's ERP Excel mapping (column heading -> field), an ERP UI field by its own name, and
otherwise by a name match. Read-only.
"""
from __future__ import annotations

import re

from app.core.erp_ui_fields import ERP_UI_SHEET


def _n(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def preview_values(fields: list, boxes: dict, plans: list, marks: list, cfs: list, wb_doc_id: str | None) -> dict:
    """{designer ref ("m:<mark id>" / "c:<custom field id>"): {"value": str, "from": "sample" |
    "excel" | "fixed"}} for this document's marks and the template's custom fields."""
    heading_to_label: dict[tuple, str] = {}
    for p in plans:
        for c in p.get("columns") or []:
            if (c.get("field") or "").strip() and (c.get("header") or "").strip():
                heading_to_label[(p.get("sheet"), c["header"].strip().lower())] = c["field"].strip()

    by_label: dict[str, str] = {}
    by_norm: dict[str, str] = {}
    for m in marks:
        ref = f"m:{m.id}"
        by_label.setdefault(m.label_name, ref)
        for name in (m.label_name, getattr(m, "display_label", None)):
            if _n(name):
                by_norm.setdefault(_n(name), ref)
    for c in cfs:
        ref = f"c:{c.id}"
        by_label.setdefault(c.label_name, ref)
        for name in (c.label_name, getattr(c, "display_label", None)):
            if _n(name):
                by_norm.setdefault(_n(name), ref)

    out: dict[str, dict] = {}
    # This document's own fields first: their sample reading is the one that belongs here.
    for f in sorted(fields, key=lambda f: f.source_document != wb_doc_id):
        if f.sheet_name == ERP_UI_SHEET:
            label = f.excel_name
        else:
            label = heading_to_label.get((f.sheet_name, (f.excel_name or "").strip().lower()))
        ref = (by_label.get(label) if label else None) or by_norm.get(_n(f.excel_name)) \
            or by_norm.get(_n(getattr(f, "display_label", None)))
        if not ref or ref in out:
            continue
        box = boxes.get(f.id)
        if box is not None and box.doc_id == wb_doc_id and (box.detected_value or "").strip():
            out[ref] = {"value": box.detected_value.strip(), "from": "sample"}
        elif f.samples:
            out[ref] = {"value": str(f.samples[0]), "from": "excel"}
        elif f.category == "fixed" and f.default_value:
            out[ref] = {"value": f.default_value, "from": "fixed"}
    return out

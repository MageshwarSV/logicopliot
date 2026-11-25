"""Customer Onboarding - "Import current setup": fill Steps 1, 2 and 4 from a template that
already runs on Excel entry (TemplateGroup.excel_config + its stored ERP workbook).

Reads the template only; writes only the onboarding tables. For each ERP Excel column the
template's own mapping decides the category:

  fixed value / values in the mapping, or a hardcoded custom field   -> fixed
  a document mark                                                    -> document (its document)
  a mark / field resolved through a reference table, a lookup        -> reference
  a field the operator answers (no AI prompt)                        -> operator
  a field the AI works out                                           -> ai
  a join (composite), a rule (pick_by), a transform, a serial (#row) -> formula
  left blank, or mapped to a field the template does not have        -> not classified
"""
from __future__ import annotations

import re

from app.core.erp_ui_fields import master_of, reference_config

ADDITIONAL_ID = "additional-details"


def field_index(marks: list, custom_fields: list) -> dict:
    """label -> {"type": "mark", "marks": [...]} | {"type": "cf", "cf": cf}. Marks first,
    as the template resolves a label to a document reading before a custom field."""
    idx: dict = {}
    for m in marks:
        idx.setdefault(m.label_name, {"type": "mark", "marks": []})
        if idx[m.label_name]["type"] == "mark":
            idx[m.label_name]["marks"].append(m)
    for c in custom_fields:
        idx.setdefault(c.label_name, {"type": "cf", "cf": c})
    return idx


def _literals(col: dict) -> list[str] | None:
    vals = col.get("values")
    if isinstance(vals, list) and vals:
        return ["" if v is None else str(v) for v in vals]
    v = col.get("value")
    if v is not None and str(v) != "":
        return [str(v)]
    return None


def _operator_config(obj) -> dict:
    opts = [str(o) for o in (getattr(obj, "fixed_options", None) or []) if str(o).strip()]
    if opts:
        input_type = "dropdown"
    elif getattr(obj, "date_picker", False):
        input_type = "date"
    else:
        input_type = "text"
    default = str(getattr(obj, "hardcoded_value", "") or "")
    if input_type == "dropdown" and default and default not in opts:
        opts.append(default)
    return {"input_type": input_type, "choices": opts if input_type == "dropdown" else [], "default": default}


def classify_column(col: dict | None, idx: dict, doc_order: list[str], doc_names: dict | None = None) -> dict:
    """What one ERP Excel column becomes in onboarding. Returns a dict with category (None =
    leave unclassified), source_document (template document id / ADDITIONAL_ID / None) and the
    settings to carry over (display_label, description, mandatory, formula, ai_prompt,
    erp_master_ref, config, line_item, mark)."""
    out: dict = {"category": None, "source_document": None}
    if not col:
        return out
    field = (col.get("field") or "").strip()
    lits = _literals(col)
    transform = col.get("transform")

    def done(category, doc=ADDITIONAL_ID, **kw):
        out.update(category=category, source_document=doc, **kw)
        return out

    if lits and not field:
        placeholders = [p for v in lits for p in re.findall(r"\[\[(.+?)\]\]", v)]
        value = lits[0] if len(lits) == 1 else " | ".join(lits)
        note = ("One value per row: " + " | ".join(lits)) if len(lits) > 1 else None
        if placeholders:
            note = (note + ". " if note else "") + "Inserts the value of: " + ", ".join(placeholders)
        return done("fixed", config={"value": value, "per_importer": [], "hidden": False}, description=note)
    if col.get("pick_by"):
        p = col["pick_by"]
        options = list((p.get("options") or {}).values())
        return done("formula", formula=f"{p.get('field')} decides which field is used: " + " / ".join(options))
    if not field:
        return out
    if field.startswith("#"):
        return done("formula", formula={"#row": "row number (1, 2, 3 ...)",
                                        "#invoice": "number of the invoice the row belongs to"}.get(field, field))
    entry = idx.get(field)
    if entry is None:
        out["description"] = f"The template maps this column to '{field}', which it does not have - left unclassified."
        return out

    if entry["type"] == "mark":
        marks = entry["marks"]
        wanted = col.get("from_document")
        by_doc = {m.document_id: m for m in marks}
        drawn = [d for d in doc_order if d in by_doc and by_doc[d].width and by_doc[d].height]
        doc = next((d for d in doc_order if d in by_doc and wanted and (doc_names or {}).get(d) == wanted), None)
        doc = doc or (drawn[0] if drawn else next((d for d in doc_order if d in by_doc), None))
        mark = by_doc.get(doc) if doc else marks[0]
        common = {"display_label": getattr(mark, "display_label", None) or None}
        if transform:
            return done("formula", formula=f"{transform}({field})", **common)
        if getattr(mark, "is_target_value", False):
            return done("reference", erp_master_ref=f"Reads '{field}' off the document, then matches it in its reference table",
                        config=reference_config(field, mark), **common)
        if getattr(mark, "ask_operator", False):
            return done("operator", mandatory=bool(getattr(mark, "ask_operator_required", False)),
                        description=getattr(mark, "ask_operator_hint", None) or None, config=_operator_config(mark), **common)
        return done("document", doc=doc, mark=mark if (mark and mark.width and mark.height) else None,
                    line_item=bool(getattr(mark, "is_multi_value", False)), **common)

    cf = entry["cf"]
    kind = cf.kind
    master = master_of(field)
    common = {"display_label": getattr(cf, "display_label", None) or (master["display"] if master else None)}
    if transform:
        return done("formula", formula=f"{transform}({field})", **common)
    if kind == "composite":
        pieces = []
        for piece in cf.composite_source_labels or []:
            pieces.append(f'"{piece.get("fixed", "")}"' if isinstance(piece, dict) else piece)
        return done("formula", formula=" + ".join(pieces) or field, **common)
    if kind == "lookup":
        return done("reference", erp_master_ref=f"Looks '{field}' up by {cf.lookup_key_label or 'part code'} in the customer's master / reference table",
                    config=reference_config(field, cf), **common)
    if kind == "ai":
        if getattr(cf, "is_target_value", False):
            return done("reference", ai_prompt=cf.ai_prompt, erp_master_ref="The AI reads it, then its reference table resolves it",
                        config=reference_config(field, cf), **common)
        if getattr(cf, "ask_operator", False) and not cf.ai_prompt:
            return done("operator", mandatory=bool(cf.ask_operator_required), description=cf.ask_operator_hint or None,
                        config=_operator_config(cf), **common)
        return done("ai", ai_prompt=cf.ai_prompt, **common)
    # hardcoded
    if getattr(cf, "is_target_value", False) or cf.lookup_key_label:
        keys = f" keyed on {cf.lookup_key_label}" if cf.lookup_key_label else ""
        how = ("The AI decides, then its reference table" if cf.ai_prompt else "Reference table") + keys
        if cf.ask_operator:
            how += "; the operator picks or confirms"
        if master:
            how = {"quotation_master": "Quotation Master", "importer_master": "Importer Master"}[master["source"]] \
                + f" ({master['level']})" + (f", within {master['depends_on']}" if master.get("depends_on") else "")
        return done("reference", erp_master_ref=how, ai_prompt=cf.ai_prompt or None,
                    config=reference_config(field, cf), **common)
    if cf.ask_operator:
        return done("operator", mandatory=bool(cf.ask_operator_required), description=cf.ask_operator_hint or None,
                    config=_operator_config(cf), **common)
    return done("fixed", config={"value": cf.hardcoded_value or "", "per_importer": [], "hidden": bool(cf.hidden_from_capture)}, **common)

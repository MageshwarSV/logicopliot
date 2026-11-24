"""Customer Onboarding - fields the ERP UI script fills on screen that are not ERP Excel columns.

Some values never go into the Excel: the script picks them in the ERP's own screen (ERP Quotation
Name / Branch, ERP Importer Name / Branch, the ERP branch). The job still needs them, so Step 1
lists them on a fixed pseudo-sheet, ERP_UI_SHEET, next to the real worksheets. They are found by
reading the ERP script's steps: every job field a step types, picks or decides from, less the
fields the template's Excel mapping already carries.

Read-only helpers: nothing here writes to a template or a script.
"""
from __future__ import annotations

import re

ERP_UI_SHEET = "ERP UI (not in Excel)"

# The template fields behind the two ERP masters (Tenant Admin -> ERP Master Data), as named
# by app/api/v1/master_data_quotation.py and master_data_importer.py: name -> branch -> address.
# The job screen shows them under the display names given here.
MASTER_FIELDS: dict[str, dict] = {
    "erp entry name": {"source": "quotation_master", "level": "name", "display": "ERP Quotation Name"},
    "branch_name": {"source": "quotation_master", "level": "branch", "display": "ERP Quotation Branch",
                    "depends_on": "Erp Entry Name", "fills": "quotation_address"},
    "quotation_address": {"source": "quotation_master", "level": "address", "display": "ERP Quotation Address",
                          "depends_on": "branch_name"},
    "erp consignee name": {"source": "importer_master", "level": "name", "display": "ERP Importer Name"},
    "erp importer address / branch code": {"source": "importer_master", "level": "branch", "display": "ERP Importer Branch",
                                           "depends_on": "ERP Consignee Name", "fills": "ERP Branch Address"},
    "erp branch address": {"source": "importer_master", "level": "address", "display": "ERP Importer Address",
                           "depends_on": "ERP Importer Address / Branch Code"},
}

_PLACEHOLDER = re.compile(r"\[\[(.+?)\]\]")
_NESTED = ("then_steps", "else_steps", "row_steps", "end_steps")


def master_of(label: str | None) -> dict | None:
    return MASTER_FIELDS.get((label or "").strip().lower())


def script_labels(steps: list | None, branch_field: str | None = None) -> list[str]:
    """Every job field the script uses, in step order, each once: a step's field pill, the
    [[placeholders]] in its value or AI rule, its value_fields, the steps inside if / else and
    per-row blocks, and the script's branch field."""
    out: list[str] = []

    def add(label) -> None:
        label = str(label or "").strip()
        if label and label not in out:
            out.append(label)

    def walk(items) -> None:
        for s in items or []:
            if not isinstance(s, dict):
                continue
            add(s.get("field_label"))
            for k in ("value", "prompt"):
                for p in _PLACEHOLDER.findall(str(s.get(k) or "")):
                    add(p)
            for v in s.get("value_fields") or []:
                add(v)
            for k in _NESTED:
                walk(s.get(k))

    walk(steps)
    add(branch_field)
    return out


def excel_labels(excel_config: dict | None) -> set[str]:
    """The job fields the template's ERP Excel mapping already carries (they are columns)."""
    from app.core.excel_entry import sheet_plans

    out: set[str] = set()
    for p in sheet_plans(excel_config or {}):
        for c in p.get("columns") or []:
            if (c.get("field") or "").strip():
                out.add(c["field"].strip())
            pick = c.get("pick_by") or {}
            if pick.get("field"):
                out.add(str(pick["field"]).strip())
            for v in (pick.get("options") or {}).values():
                if v:
                    out.add(str(v).strip())
            for f in c.get("fallback_fields") or []:
                if f:
                    out.add(str(f).strip())
    return out


def with_parents(labels: list[str], known: set[str]) -> list[str]:
    """A branch is picked within its name: when the script picks a master's branch, the name it
    depends on is needed too (ERP Importer Branch needs ERP Importer Name). Parents go first,
    and only when the template has that field."""
    out: list[str] = []

    def add(label: str, depth: int = 0) -> None:
        m = master_of(label)
        if m and m.get("depends_on") and m["depends_on"] in known and depth < 5:
            add(m["depends_on"], depth + 1)
        if label not in out:
            out.append(label)

    for label in labels:
        add(label)
    return out


def reference_config(label: str | None, obj=None) -> dict:
    """Step 4 Reference configuration as the template runs it today."""
    m = master_of(label)
    pick = "operator" if getattr(obj, "ask_operator", False) else "auto"
    if m:
        return {"source": m["source"], "level": m["level"], "depends_on": m.get("depends_on", ""),
                "fills": m.get("fills", ""), "key_field": "", "pick": pick}
    return {"source": "own_table", "level": "", "depends_on": "", "fills": "",
            "key_field": getattr(obj, "lookup_key_label", None) or "", "pick": pick}

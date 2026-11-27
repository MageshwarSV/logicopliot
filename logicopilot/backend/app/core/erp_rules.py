"""ERP column rules (P1 + P2, 2026-11-27): what each ERP Excel column accepts, checked BEFORE
the workbook goes to the ERP, so a value the ERP would refuse never reaches it.

Rules belong to the ERP column (sheet + header), not to the Cargora box: one box can feed two
columns (Country of Origin -> Country_of_Origin and COO_Issuing_Country), and each column's
rule applies. Stored per template on TemplateGroup.erp_column_rules, keyed "SHEET|Header":

    {"required": bool, "length": int, "length_mode": "exact" | "max", "master": "country" |
     "currency" | "unit", "number": bool, "pattern": "regex", "message": "shown when the
     pattern fails", "uppercase": bool, "off": bool}

Built in (no setup): every column whose header names a country code (Country_of_Origin,
COO_Issuing_Country, CountryOfOriginCode, CountryOfShipmentCode, MFG_Country,
Supplier_Country_Code ...) must be a 2-letter ISO country code - the rule SOFTLINK enforced on
JOB-E6C19F and again the next day. A stored rule replaces the built-in one; "off" removes it.

Two effects:
  effective_config()  - a copy of the template's Excel config where a country-code column
                        turns a country written in full into its code when certain
                        ("POLAND" -> "PL") and an uppercase column upper-cases. Per COLUMN,
                        inside the build: the same value feeding another column is untouched.
  check()             - every cell of the workbook as it would be built, against the rules.
                        What fails is reported in the ERP-rejection card's own shape
                        (erp_rejection.build_items), so the operator gets Fix -> box in red.
"""
from __future__ import annotations

import copy
import re

from sqlalchemy.orm import Session

from app.core.erp_masters import MASTERS

COUNTRY_TRANSFORM = "country_iso2"
CURRENCY_TRANSFORM = "currency_code"
UPPER_TRANSFORM = "upper"
_NO_COUNTRY_RULE = ("certificate", "percent", "amount", "%", "name", "address")


def _norm(s) -> str:
    return re.sub(r"[\s_\-.]+", "", str(s or "")).lower()


def rule_key(sheet: str | None, header: str | None) -> str:
    return f"{(sheet or '').strip()}|{(header or '').strip()}"


def builtin_rule(header: str | None) -> dict | None:
    """The rule a column gets without any setup, or None."""
    h = str(header or "").lower()
    n = _norm(header)
    if "country" in n and not any(w in h for w in _NO_COUNTRY_RULE):
        return {"master": "country", "builtin": True}
    # Inv_Currency, Frt_Currency, Misc_Charge_Currency, CURRENCY_CODE ... - 3-letter codes
    # (SOFTLINK: "Entry should be of 3 characters", 2026-11-27). Production held only valid
    # codes or blanks in every one of them over the 14 days before this was added.
    if "currency" in n:
        return {"master": "currency", "builtin": True}
    return None


def _columns(cfg: dict):
    from app.core.excel_entry import sheet_plans

    for plan in sheet_plans(cfg or {}):
        for col in plan["columns"]:
            header = col.get("header") or col.get("field") or ""
            yield plan, col, header


def effective_rules(group) -> dict[str, dict]:
    """{key: rule} for every column of the template's Excel config that has one."""
    stored = {k: v for k, v in ((getattr(group, "erp_column_rules", None) or {}).items()) if isinstance(v, dict)}
    out: dict[str, dict] = {}
    for plan, _col, header in _columns(getattr(group, "excel_config", None) or {}):
        key = rule_key(plan.get("sheet"), header)
        rule = stored.get(key)
        if rule is not None:
            if rule.get("off"):
                continue
            out[key] = {**rule, "builtin": False}
            continue
        b = builtin_rule(header)
        if b:
            out[key] = b
    return out


def effective_config(group) -> dict:
    """The template's Excel config with the per-column conversions the rules call for."""
    cfg = copy.deepcopy(getattr(group, "excel_config", None) or {})
    if not cfg:
        return cfg
    rules = effective_rules(group)
    if not rules:
        return cfg
    from app.core.excel_entry import sheet_plans

    raw_sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), list) and cfg.get("sheets") else None
    containers = raw_sheets if raw_sheets else [cfg]
    for i, container in enumerate(containers):
        sheet = container.get("sheet") if raw_sheets else sheet_plans(cfg)[0].get("sheet")
        for col in container.get("columns") or []:
            if not isinstance(col, dict) or col.get("transform"):
                continue
            rule = rules.get(rule_key(sheet, col.get("header") or col.get("field") or ""))
            if not rule:
                continue
            if rule.get("master") == "country":
                col["transform"] = COUNTRY_TRANSFORM
            elif rule.get("master") == "currency":
                col["transform"] = CURRENCY_TRANSFORM
            elif rule.get("uppercase"):
                col["transform"] = UPPER_TRANSFORM
    return cfg


def describe(rule: dict) -> str:
    """The rule in words an operator understands."""
    if rule.get("master") in MASTERS:
        return MASTERS[rule["master"]][0]
    parts = []
    if rule.get("required"):
        parts.append("must not be empty")
    if rule.get("number"):
        parts.append("a number")
    if rule.get("length"):
        n = int(rule["length"])
        parts.append(f"exactly {n} characters" if (rule.get("length_mode") or "max") == "exact" else f"at most {n} characters")
    if rule.get("pattern"):
        parts.append(rule.get("message") or "in the format the ERP expects")
    text = ", ".join(parts) or "a value the ERP accepts"
    return text[:1].upper() + text[1:] + "."


def validate(value, rule: dict) -> str | None:
    """Why the value breaks the rule (in plain words), or None when it is fine."""
    v = "" if value is None else str(value).strip()
    if not v:
        return "This must not be empty." if rule.get("required") else None
    master = rule.get("master")
    if master in MASTERS:
        return None if MASTERS[master][1](v) else MASTERS[master][0]
    if rule.get("number"):
        try:
            float(v.replace(",", ""))
        except ValueError:
            return "A number (digits only)."
    if rule.get("length"):
        n = int(rule["length"])
        exact = (rule.get("length_mode") or "max") == "exact"
        if exact and len(v) != n:
            return f"Exactly {n} characters."
        if not exact and len(v) > n:
            return f"At most {n} characters (this has {len(v)})."
    if rule.get("pattern"):
        try:
            if not re.fullmatch(rule["pattern"], v):
                return (rule.get("message") or "Not in the format the ERP expects.").rstrip(".") + "."
        except re.error:
            return None          # a broken pattern never blocks a job
    return None


def check(db: Session, job, group, values: dict, rows: dict) -> list[dict]:
    """Every cell that breaks a rule, as ERP-rejection rows:
    [{"sheet", "column", "invoice", "product", "rule", "text"}]."""
    from app.core.excel_entry import _rows_for_plan, invoice_of, sheet_plans

    rules = effective_rules(group)
    if not rules:
        return []
    cfg = effective_config(group)
    out: list[dict] = []
    for plan in sheet_plans(cfg):
        cols = plan["columns"]
        wanted = [(ci, rules.get(rule_key(plan.get("sheet"), c.get("header") or c.get("field") or "")))
                  for ci, c in enumerate(cols)]
        wanted = [(ci, r) for ci, r in wanted if r]
        if not wanted:
            continue
        try:
            data = _rows_for_plan(plan, values, rows)
        except Exception:  # noqa: BLE001 - the build itself reports a broken mapping
            continue
        seen_in_invoice: dict[int, int] = {}
        for i, line in enumerate(data):
            inv = prod = None
            if plan["scope"] == "line":
                inv = invoice_of(i, values, rows)
                seen_in_invoice[inv] = seen_in_invoice.get(inv, 0) + 1
                prod = seen_in_invoice[inv]
            elif plan["scope"] == "invoice":
                inv = i + 1
            for ci, rule in wanted:
                why = validate(line[ci] if ci < len(line) else "", rule)
                if why:
                    header = cols[ci].get("header") or cols[ci].get("field") or ""
                    out.append({"sheet": plan.get("sheet") or "", "column": header, "invoice": inv,
                                "product": prod, "rule": why,
                                "text": f"{plan.get('sheet')} {header} {why}"})
    return out


def pre_check(db: Session, job, values: dict, rows: dict) -> dict | None:
    """The card for what would be refused, or None when everything passes (or the template is
    not Excel entry, or a hand-edited Excel replaces the generated one)."""
    from app.core.erp_excel_edit import edited_file
    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id) if job.group_id else None
    if group is None or (group.entry_mode or "fields") != "excel" or edited_file(job.id) is not None:
        return None
    problems = check(db, job, group, values, rows)
    if not problems:
        return None
    from app.core.erp_rejection import build_items

    items = build_items(db, job, problems)
    return {"kind": "pre_check", "items": items, "erp_rows": problems, "excel_edited": False, "ai": False}


def summary(help_: dict) -> str:
    items = help_.get("items") or []
    parts = [f"{i.get('display')}{' (' + i['where'] + ')' if i.get('where') else ''}: {i.get('needs')}" for i in items[:3]]
    more = f" (and {len(items) - 3} more)" if len(items) > 3 else ""
    return ("Not sent to the ERP - it would refuse these values. Fix them and submit again: "
            + " | ".join(parts) + more)[:2000]


# ---------------------------------------------------------------- P3: rejections -> suggestions

SUGGESTED_KEY = "_suggested"
DISMISSED_KEY = "_dismissed"
_LEN = re.compile(r"should be (?:of\s+)?(\d+)\s+char", re.I)
_REQUIRED = ("mandatory", "required", "cannot be blank", "can not be blank", "should not be blank",
             "should not be empty", "cannot be empty")
_UNKNOWN = ("not available", "does not exist", "not found", "invalid", "not valid", "not in")


def infer_rule(erp_text: str) -> dict | None:
    """A rule from the ERP's own wording, or {"needs_list": True} when the ERP refused a
    value it does not know (only a person can give the list), or None."""
    t = (erp_text or "").lower()
    m = _LEN.search(t)
    if m:
        return {"length": int(m.group(1)), "length_mode": "exact"}
    if any(w in t for w in _REQUIRED):
        return {"required": True}
    if any(w in t for w in _UNKNOWN):
        return {"needs_list": True}
    return None


def _covered(rule: dict, active: dict | None) -> bool:
    """The column's current rule already stops what the suggestion would."""
    if not active:
        return False
    if rule.get("required"):
        return bool(active.get("required"))
    if rule.get("length"):
        n = rule["length"]
        if active.get("master") == "country" and n == 2:
            return True
        if active.get("master") == "currency" and n == 3:
            return True
        return active.get("length") == n and (active.get("length_mode") or "max") == "exact"
    return False


def record_suggestions(db: Session, job, help_: dict) -> int:
    """After the ERP refused an import: remember, on the template, a rule per refused column so
    a Super Admin can add it with one click (ERP Column Rules). Returns how many are new.
    Never raises into the run (the caller wraps it)."""
    from datetime import datetime, timezone

    from app.models.template_group import TemplateGroup

    group = db.get(TemplateGroup, job.group_id) if job.group_id else None
    if group is None or not (help_ or {}).get("erp_rows"):
        return 0
    keys = {(_norm(p.get("sheet")), _norm(h)): rule_key(p.get("sheet"), h)
            for p, _c, h in _columns(group.excel_config or {})}
    stored = dict(group.erp_column_rules or {})
    suggested = dict(stored.get(SUGGESTED_KEY) or {})
    dismissed = set(stored.get(DISMISSED_KEY) or [])
    active = effective_rules(group)
    new = 0
    for row in help_["erp_rows"]:
        key = keys.get((_norm(row.get("sheet")), _norm(row.get("column"))))
        rule = infer_rule(row.get("rule") or row.get("text") or "")
        if not key or not rule:
            continue
        sig = f"{key}::{sorted(rule.items())}"
        if sig in dismissed or _covered(rule, active.get(key)):
            continue
        cur = suggested.get(key)
        if cur and cur.get("rule") == rule:
            cur["count"] = int(cur.get("count") or 1) + (0 if cur.get("last_job") == job.reference else 1)
        else:
            cur = {"rule": rule, "count": 1}
            new += 1
        cur.update({"erp_text": (row.get("rule") or "")[:200], "last_job": job.reference,
                    "last_at": datetime.now(timezone.utc).isoformat()})
        suggested[key] = cur
    if suggested != (stored.get(SUGGESTED_KEY) or {}):
        stored[SUGGESTED_KEY] = suggested
        group.erp_column_rules = stored
    return new

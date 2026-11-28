"""ERP Column Rules (P2, 2026-11-27) - Super Admin sets what each ERP Excel column accepts,
per template; app/core/erp_rules.py applies them before the workbook goes to the ERP.

  GET  /erp-rules/templates                     Excel-entry templates
  GET  /erp-rules/{group_id}                    every column, its built-in and saved rule
  PUT  /erp-rules/{group_id}                    save the rules ({"rules": {"SHEET|Header": {...}}})
  POST /erp-rules/{group_id}/copy-from/{other}  take another template's rules (same ERP)
  GET  /erp-rules/{group_id}/check?reference=   what a real job would be stopped for, now
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, require_role
from app.core.erp_masters import MASTERS
from app.core.erp_rules import (DISMISSED_KEY, SUGGESTED_KEY, _columns, builtin_rule, describe,
                                 effective_rules, rule_key)
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/erp-rules", tags=["erp rules"])
_admin = require_role(SUPER_ADMIN)

_ALLOWED = {"required", "length", "length_mode", "master", "number", "pattern", "message", "uppercase", "off",
            "allowed", "length_when"}


def _group(db: Session, group_id: str) -> TemplateGroup:
    g = db.get(TemplateGroup, group_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return g


def _clean(rule: dict) -> dict | None:
    """Only known keys, sane values; None when the rule says nothing."""
    import re

    out: dict = {}
    for k, v in (rule or {}).items():
        if k not in _ALLOWED or v in (None, "", False):
            continue
        if k == "length":
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Length must be a whole number")
            if n <= 0:
                continue
            out[k] = n
        elif k == "length_mode":
            out[k] = "exact" if v == "exact" else "max"
        elif k == "master":
            if v not in MASTERS:
                raise HTTPException(status_code=422, detail=f"Unknown list: {v}")
            out[k] = v
        elif k == "pattern":
            try:
                re.compile(str(v))
            except re.error as exc:
                raise HTTPException(status_code=422, detail=f"Pattern is not valid: {exc}")
            out[k] = str(v)[:300]
        elif k == "message":
            out[k] = str(v)[:200]
        elif k == "allowed":
            vals = v if isinstance(v, list) else re.split(r"[,\n]", str(v))
            vals = list(dict.fromkeys(str(x).strip().upper() for x in vals if str(x).strip()))[:300]
            if vals:
                out[k] = vals
        elif k == "length_when":
            if not isinstance(v, dict) or not v.get("column") or not isinstance(v.get("lengths"), dict):
                raise HTTPException(status_code=422, detail="length_when needs a column and lengths")
            try:
                lengths = {str(a).strip().upper(): int(n) for a, n in v["lengths"].items() if int(n) > 0}
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="length_when lengths must be whole numbers")
            out[k] = {"column": str(v["column"])[:120], "lengths": lengths}
        else:
            out[k] = bool(v)
    if "length_mode" in out and "length" not in out:
        out.pop("length_mode")
    return out or None


@router.get("/masters/{kind}")
def masters(kind: str, _u: User = Depends(get_current_user)) -> list[dict]:
    """The country / currency list for the job screen's search dropdown (any signed-in user)."""
    from app.core.erp_masters import master_list

    if kind not in ("country", "currency"):
        raise HTTPException(status_code=404, detail="Unknown list")
    return master_list(kind)


@router.get("/templates")
def templates(db: Session = Depends(get_db), _u: User = Depends(_admin)) -> list[dict]:
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    rows = [g for g in db.query(TemplateGroup).all() if (g.entry_mode or "fields") == "excel" and g.excel_config]
    return sorted(({"id": g.id, "name": g.name, "tenant": tenants.get(g.tenant_id) or "", "mode": g.mode,
                    "rules_saved": len([k for k in (g.erp_column_rules or {}) if not k.startswith("_")]),
                    "suggestions": len((g.erp_column_rules or {}).get(SUGGESTED_KEY) or {})} for g in rows),
                  key=lambda r: (r["tenant"].lower(), r["name"].lower()))


@router.get("/{group_id}")
def columns(group_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    g = _group(db, group_id)
    stored = g.erp_column_rules or {}
    active = effective_rules(g)
    cols = []
    for plan, col, header in _columns(g.excel_config or {}):
        key = rule_key(plan.get("sheet"), header)
        b = builtin_rule(header)
        lit = col.get("value") if col.get("value") not in (None, "") else None
        cols.append({
            "key": key, "sheet": plan.get("sheet") or "", "header": header,
            "field": col.get("field") or "", "fixed_value": lit,
            "builtin": b, "builtin_text": describe(b) if b else None,
            "rule": stored.get(key), "active_text": describe(active[key]) if key in active else None,
        })
    by_key = {c["key"]: c for c in cols}
    suggestions = []
    for key, sug in ((stored.get(SUGGESTED_KEY) or {}).items()):
        c = by_key.get(key)
        if c is None:
            continue
        rule = sug.get("rule") or {}
        suggestions.append({"key": key, "sheet": c["sheet"], "header": c["header"], "field": c["field"],
                            "rule": rule, "rule_text": ("Needs a list or pattern - the ERP refused a value it does not know"
                                                        if rule.get("needs_list") else describe(rule)),
                            "erp_text": sug.get("erp_text"), "count": sug.get("count") or 1,
                            "last_job": sug.get("last_job"), "last_at": sug.get("last_at")})
    suggestions.sort(key=lambda x: x.get("last_at") or "", reverse=True)
    return {"id": g.id, "name": g.name, "columns": cols, "suggestions": suggestions,
            "masters": {k: v[0] for k, v in MASTERS.items()}}


class RulesIn(BaseModel):
    rules: dict[str, dict]


@router.put("/{group_id}")
def save(group_id: str, payload: RulesIn, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    g = _group(db, group_id)
    keys = {rule_key(p.get("sheet"), h) for p, _c, h in _columns(g.excel_config or {})}
    out = {}
    for key, rule in (payload.rules or {}).items():
        if key not in keys:
            continue                      # a column no longer in the mapping
        cleaned = _clean(rule)
        if cleaned:
            out[key] = cleaned
    # suggestions and dismissals are not rules - kept as they were
    for k, v in (g.erp_column_rules or {}).items():
        if k.startswith("_"):
            out[k] = v
    g.erp_column_rules = out or None
    db.commit()
    return {"saved": len(out)}


@router.post("/{group_id}/copy-from/{other_id}")
def copy_from(group_id: str, other_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    g, other = _group(db, group_id), _group(db, other_id)
    mine = {rule_key(p.get("sheet"), h) for p, _c, h in _columns(g.excel_config or {})}
    taken = {k: v for k, v in (other.erp_column_rules or {}).items() if k in mine and not k.startswith("_")}
    g.erp_column_rules = {**(g.erp_column_rules or {}), **taken} or None
    db.commit()
    theirs = [k for k in (other.erp_column_rules or {}) if not k.startswith("_")]
    return {"copied": len(taken), "skipped": len(theirs) - len(taken)}


@router.get("/{group_id}/check")
def check_job(group_id: str, reference: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    from app.api.v1.jobs import entry_values_and_rows
    from app.core.erp_rejection import build_items
    from app.core.erp_rules import check

    g = _group(db, group_id)
    job = db.query(Job).filter(Job.reference == reference.strip(), Job.group_id == g.id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="No job with that number on this template")
    values, rows = entry_values_and_rows(db, job)
    problems = check(db, job, g, values, rows)
    return {"reference": job.reference, "problems": problems,
            "items": build_items(db, job, problems) if problems else []}


class SuggestionIn(BaseModel):
    key: str


@router.post("/{group_id}/suggestions/accept")
def accept_suggestion(group_id: str, payload: SuggestionIn, db: Session = Depends(get_db),
                      _u: User = Depends(_admin)) -> dict:
    """Add a suggested rule to the column's rule (merged with what is there)."""
    g = _group(db, group_id)
    stored = dict(g.erp_column_rules or {})
    sugs = dict(stored.get(SUGGESTED_KEY) or {})
    sug = sugs.pop(payload.key, None)
    if sug is None:
        raise HTTPException(status_code=404, detail="No such suggestion")
    rule = {k: v for k, v in (sug.get("rule") or {}).items() if k != "needs_list"}
    if not rule:
        raise HTTPException(status_code=422, detail="This one needs a list or pattern - set it on the column itself")
    stored[payload.key] = _clean({**(stored.get(payload.key) or {}), **rule}) or {}
    stored[SUGGESTED_KEY] = sugs
    g.erp_column_rules = stored
    db.commit()
    return {"ok": True, "rule": stored[payload.key]}


@router.post("/{group_id}/suggestions/dismiss")
def dismiss_suggestion(group_id: str, payload: SuggestionIn, db: Session = Depends(get_db),
                       _u: User = Depends(_admin)) -> dict:
    """Not a rule for this template - the same suggestion is not raised again."""
    g = _group(db, group_id)
    stored = dict(g.erp_column_rules or {})
    sugs = dict(stored.get(SUGGESTED_KEY) or {})
    sug = sugs.pop(payload.key, None)
    if sug is None:
        raise HTTPException(status_code=404, detail="No such suggestion")
    dismissed = list(stored.get(DISMISSED_KEY) or [])
    dismissed.append(f"{payload.key}::{sorted((sug.get('rule') or {}).items())}")
    stored[SUGGESTED_KEY] = sugs
    stored[DISMISSED_KEY] = dismissed[-500:]
    g.erp_column_rules = stored
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- R7: ERP Field Configuration

@router.get("/{group_id}/config")
def field_config(group_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    """Every ERP column: document -> field -> mandatory -> rules -> Excel file / sheet / column
    -> ERP field -> mapping status (app/core/erp_field_map.py)."""
    from app.core.erp_field_map import config_rows

    g = _group(db, group_id)
    return {"id": g.id, "name": g.name, "masters": {k: v[0] for k, v in MASTERS.items()}, **config_rows(db, g)}


@router.post("/{group_id}/analyse")
def analyse_jobs(group_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    """Measure, on recent jobs the ERP accepted, how often each column had a value."""
    from app.core.erp_field_map import analyse

    return analyse(db, _group(db, group_id))


class MandatoryIn(BaseModel):
    keys: list[str]
    required: bool = True


@router.post("/{group_id}/mandatory")
def set_mandatory(group_id: str, payload: MandatoryIn, db: Session = Depends(get_db),
                  _u: User = Depends(_admin)) -> dict:
    """Mark (or unmark) several columns mandatory at once - the column's rule "required",
    the one meaning of mandatory the pre-ERP check uses. Other parts of a rule are kept."""
    g = _group(db, group_id)
    keys = {rule_key(p.get("sheet"), h) for p, _c, h in _columns(g.excel_config or {})}
    stored = dict(g.erp_column_rules or {})
    changed = 0
    for key in payload.keys:
        if key not in keys:
            continue
        rule = dict(stored.get(key) or {})
        if bool(rule.get("required")) == payload.required:
            continue
        if payload.required:
            rule["required"] = True
        else:
            rule.pop("required", None)
        cleaned = _clean(rule)
        if cleaned:
            stored[key] = cleaned
        else:
            stored.pop(key, None)
        changed += 1
    g.erp_column_rules = stored or None
    db.commit()
    return {"changed": changed}



@router.get("/{group_id}/fields")
def field_view(group_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    """The configuration by document and field (the operator's view) - app/core/erp_field_map.field_rows."""
    from app.core.erp_field_map import field_rows

    g = _group(db, group_id)
    return {"id": g.id, "name": g.name, **field_rows(db, g)}

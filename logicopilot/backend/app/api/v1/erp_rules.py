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

from app.core.deps import get_db, require_role
from app.core.erp_masters import MASTERS
from app.core.erp_rules import _columns, builtin_rule, describe, effective_rules, rule_key
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/erp-rules", tags=["erp rules"])
_admin = require_role(SUPER_ADMIN)

_ALLOWED = {"required", "length", "length_mode", "master", "number", "pattern", "message", "uppercase", "off"}


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
        else:
            out[k] = bool(v)
    if "length_mode" in out and "length" not in out:
        out.pop("length_mode")
    return out or None


@router.get("/templates")
def templates(db: Session = Depends(get_db), _u: User = Depends(_admin)) -> list[dict]:
    tenants = {t.id: t.name for t in db.query(Tenant).all()}
    rows = [g for g in db.query(TemplateGroup).all() if (g.entry_mode or "fields") == "excel" and g.excel_config]
    return sorted(({"id": g.id, "name": g.name, "tenant": tenants.get(g.tenant_id) or "", "mode": g.mode,
                    "rules_saved": len(g.erp_column_rules or {})} for g in rows),
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
    return {"id": g.id, "name": g.name, "columns": cols,
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
    g.erp_column_rules = out or None
    db.commit()
    return {"saved": len(out)}


@router.post("/{group_id}/copy-from/{other_id}")
def copy_from(group_id: str, other_id: str, db: Session = Depends(get_db), _u: User = Depends(_admin)) -> dict:
    g, other = _group(db, group_id), _group(db, other_id)
    mine = {rule_key(p.get("sheet"), h) for p, _c, h in _columns(g.excel_config or {})}
    taken = {k: v for k, v in (other.erp_column_rules or {}).items() if k in mine}
    g.erp_column_rules = {**(g.erp_column_rules or {}), **taken} or None
    db.commit()
    return {"copied": len(taken), "skipped": len(other.erp_column_rules or {}) - len(taken)}


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

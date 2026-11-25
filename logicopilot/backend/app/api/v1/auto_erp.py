"""Auto ERP Submission settings (see app/core/auto_erp.py).

  GET  /auto-erp/tenants/{tenant_id}                       SaaS Admin, or that tenant's admin
  PUT  /auto-erp/tenants/{tenant_id}/allowed               SaaS Admin only
  PUT  /auto-erp/tenants/{tenant_id}/templates/{group_id}  SaaS Admin, or that tenant's admin
                                                           (only once the tenant is allowed)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auto_erp import clean_branches
from app.core.deps import get_current_user, get_db
from app.models.erp_branch_session import ErpBranchSession
from app.models.erp_script import ErpScript
from app.models.template_group import TemplateGroup
from app.models.tenant import Tenant
from app.models.user import SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(prefix="/auto-erp", tags=["auto ERP submission"])


class AllowedIn(BaseModel):
    allowed: bool


class TemplateRuleIn(BaseModel):
    enabled: bool
    branches: list[str] = []


def _tenant(db: Session, user: User, tenant_id: str) -> Tenant:
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _out(db: Session, tenant: Tenant) -> dict:
    rules = tenant.auto_erp_rules or {}
    scripts = db.query(ErpScript).filter(ErpScript.tenant_id == tenant.id).all()
    branches = sorted({(b.branch or "").strip() for b in db.query(ErpBranchSession).filter(
        ErpBranchSession.tenant_id == tenant.id, ErpBranchSession.enabled.is_(True)).all()
        if (b.branch or "").strip()}, key=str.casefold)
    templates = []
    for g in db.query(TemplateGroup).filter(TemplateGroup.tenant_id == tenant.id).order_by(TemplateGroup.name).all():
        linked = [s for s in scripts if g.id in (s.template_ids or [])]
        ready = next((s for s in linked if s.status == "ready"), None)
        rule = rules.get(g.id) if isinstance(rules.get(g.id), dict) else {}
        excel = (g.entry_mode or "fields") == "excel"
        problems = []
        if ready is None:
            problems.append("No ready ERP script is linked to this template.")
        if not excel:
            problems.append("The template is not set up for Excel entry.")
        templates.append({
            "group_id": g.id,
            "name": g.name,
            "mode": g.mode,
            "excel_entry": excel,
            "script": ({"id": ready.id, "name": ready.name, "branch_field": ready.branch_field or None}
                       if ready else None),
            "enabled": bool(rule.get("enabled")),
            "branches": clean_branches(rule.get("branches")),
            "problems": problems,
        })
    return {"tenant_id": tenant.id, "tenant_name": tenant.name, "allowed": bool(tenant.auto_erp_allowed),
            "branch_options": branches, "templates": templates}


@router.get("/tenants/{tenant_id}")
def get_settings(tenant_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    return _out(db, _tenant(db, user, tenant_id))


@router.put("/tenants/{tenant_id}/allowed")
def set_allowed(tenant_id: str, payload: AllowedIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    """The SaaS Admin's switch. Turning it off stops every template of the tenant at once (the
    per-template choices are kept, so turning it back on restores them)."""
    if user.role != SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the SaaS Admin can allow Auto ERP Submission for a customer.")
    tenant = _tenant(db, user, tenant_id)
    tenant.auto_erp_allowed = payload.allowed
    db.commit()
    return _out(db, tenant)


@router.put("/tenants/{tenant_id}/templates/{group_id}")
def set_template_rule(tenant_id: str, group_id: str, payload: TemplateRuleIn,
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    tenant = _tenant(db, user, tenant_id)
    if not tenant.auto_erp_allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Auto ERP Submission is not allowed for this customer. Ask the SaaS Admin to allow it first.")
    g = db.get(TemplateGroup, group_id)
    if g is None or g.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    rules = dict(tenant.auto_erp_rules or {})
    rules[group_id] = {"enabled": payload.enabled, "branches": clean_branches(payload.branches)}
    tenant.auto_erp_rules = rules     # a new dict, so the JSON column is saved
    db.commit()
    return _out(db, tenant)

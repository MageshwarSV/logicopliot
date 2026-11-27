"""Manual invoices (SaaS Finance / Super Admin, 2026-11-27) - one-off bills written by hand.

  GET    /finance/manual-invoices                 all (filter tenant_id, status)
  POST   /finance/manual-invoices                 new draft
  PUT    /finance/manual-invoices/{id}            edit a draft
  DELETE /finance/manual-invoices/{id}            delete a draft (an issued one is cancelled instead)
  POST   /finance/manual-invoices/{id}/issue      next number from the shared series, locked
  POST   /finance/manual-invoices/{id}/mark-paid  {"paid": true|false}
  POST   /finance/manual-invoices/{id}/cancel     {"reason": "..."} - number stays used
  GET    /finance/manual-invoices/{id}/print      the tax invoice page's data
  GET    /tenants/{tenant_id}/billing/manual-invoices            a customer's issued ones
  GET    /tenants/{tenant_id}/billing/manual-invoices/{id}/print
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core import manual_invoices as mi
from app.core.deps import get_current_user, get_db, require_role
from app.models.manual_invoice import ManualInvoice
from app.models.tenant import Tenant
from app.models.user import SAAS_FINANCE, SUPER_ADMIN, TENANT_ADMIN, User

router = APIRouter(tags=["manual invoices"])
_finance = require_role(SUPER_ADMIN, SAAS_FINANCE)
DEFAULT_DUE_DAYS = 7


class LineIn(BaseModel):
    description: str = ""
    sac: str = ""
    qty: float = 1
    rate: float = 0


class DraftIn(BaseModel):
    tenant_id: str
    invoice_date: str | None = None
    due_date: str | None = None
    period_label: str | None = None
    lines: list[LineIn] = []
    gst_percent: float = 18.0
    notes: str | None = None


def _settings(db: Session):
    from app.models.invoice_settings import SINGLETON_ID, InvoiceSettings

    return db.get(InvoiceSettings, SINGLETON_ID) or InvoiceSettings()


def _out(inv: ManualInvoice, names: dict[str, str]) -> dict:
    return {
        "id": inv.id, "tenant_id": inv.tenant_id, "tenant_name": names.get(inv.tenant_id, "—"),
        "status": inv.status, "invoice_number": inv.invoice_number, "invoice_date": inv.invoice_date,
        "due_date": inv.due_date, "period_label": inv.period_label, "lines": inv.lines or [],
        "gst_percent": inv.gst_percent, "subtotal": inv.subtotal, "gst_amount": inv.gst_amount,
        "total_amount": inv.total_amount, "notes": inv.notes, "issued_at": inv.issued_at,
        "paid_at": inv.paid_at, "cancelled_at": inv.cancelled_at, "cancel_reason": inv.cancel_reason,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


def _names(db: Session) -> dict[str, str]:
    return {t.id: t.name for t in db.query(Tenant).all()}


def _load(db: Session, inv_id: str) -> ManualInvoice:
    inv = db.get(ManualInvoice, inv_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


def _apply(db: Session, inv: ManualInvoice, p: DraftIn) -> None:
    if db.get(Tenant, p.tenant_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        inv_date = date.fromisoformat(p.invoice_date) if p.invoice_date else date.today()
        due = date.fromisoformat(p.due_date) if p.due_date else inv_date + timedelta(days=DEFAULT_DUE_DAYS)
    except ValueError:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")
    try:
        lines = mi.clean_lines([ln.model_dump() for ln in p.lines])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if due < inv_date:
        raise HTTPException(status_code=422, detail="The due date cannot be before the invoice date")
    if not 0 <= p.gst_percent <= 28:
        raise HTTPException(status_code=422, detail="GST % must be between 0 and 28")
    inv.tenant_id = p.tenant_id
    inv.invoice_date, inv.due_date = inv_date.isoformat(), due.isoformat()
    inv.period_label = (p.period_label or "").strip()[:80] or None
    inv.lines = lines
    inv.gst_percent = p.gst_percent
    inv.notes = (p.notes or "").strip() or None
    mi.recompute(inv)


@router.get("/finance/manual-invoices")
def list_manual(tenant_id: str | None = None, status: str | None = None,
                db: Session = Depends(get_db), _u: User = Depends(_finance)) -> list[dict]:
    q = db.query(ManualInvoice)
    if tenant_id:
        q = q.filter(ManualInvoice.tenant_id == tenant_id)
    if status:
        q = q.filter(ManualInvoice.status == status)
    names = _names(db)
    return [_out(i, names) for i in q.order_by(ManualInvoice.invoice_date.desc(), ManualInvoice.created_at.desc()).all()]


@router.post("/finance/manual-invoices")
def create_draft(p: DraftIn, db: Session = Depends(get_db), user: User = Depends(_finance)) -> dict:
    inv = ManualInvoice(status="draft", created_by_id=user.id, lines=[])
    _apply(db, inv, p)
    db.add(inv)
    db.commit()
    return _out(inv, _names(db))


@router.put("/finance/manual-invoices/{inv_id}")
def update_draft(inv_id: str, p: DraftIn, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    if inv.status != "draft":
        raise HTTPException(status_code=409, detail="An issued invoice cannot be changed - cancel it and make a new one")
    _apply(db, inv, p)
    db.commit()
    return _out(inv, _names(db))


@router.delete("/finance/manual-invoices/{inv_id}")
def delete_draft(inv_id: str, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    if inv.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be deleted - cancel an issued invoice instead")
    db.delete(inv)
    db.commit()
    return {"ok": True}


@router.post("/finance/manual-invoices/{inv_id}/issue")
def issue(inv_id: str, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    tenant = db.get(Tenant, inv.tenant_id)
    try:
        mi.issue(db, inv, tenant, _settings(db))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return _out(inv, _names(db))


class PaidIn(BaseModel):
    paid: bool = True


@router.post("/finance/manual-invoices/{inv_id}/mark-paid")
def mark_paid(inv_id: str, p: PaidIn, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    if inv.status not in ("issued", "paid"):
        raise HTTPException(status_code=409, detail="Only an issued invoice can be marked paid")
    inv.status = "paid" if p.paid else "issued"
    inv.paid_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if p.paid else None
    db.commit()
    return _out(inv, _names(db))


class CancelIn(BaseModel):
    reason: str = ""


@router.post("/finance/manual-invoices/{inv_id}/cancel")
def cancel(inv_id: str, p: CancelIn, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    if inv.status not in ("issued", "paid"):
        raise HTTPException(status_code=409, detail="Only an issued invoice can be cancelled (delete a draft instead)")
    if not (p.reason or "").strip():
        raise HTTPException(status_code=422, detail="Say why it is cancelled")
    inv.status = "cancelled"
    inv.cancelled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inv.cancel_reason = p.reason.strip()[:500]
    db.commit()
    return _out(inv, _names(db))


@router.get("/finance/manual-invoices/{inv_id}/print")
def print_finance(inv_id: str, db: Session = Depends(get_db), _u: User = Depends(_finance)) -> dict:
    inv = _load(db, inv_id)
    return mi.printable(inv, db.get(Tenant, inv.tenant_id), _settings(db))


def _customer_access(user: User, tenant_id: str) -> None:
    if user.role not in (SUPER_ADMIN, TENANT_ADMIN, SAAS_FINANCE):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    if user.role == TENANT_ADMIN and user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Tenant not found")


@router.get("/tenants/{tenant_id}/billing/manual-invoices")
def list_for_customer(tenant_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    """A customer's manual invoices - drafts are never shown to them."""
    _customer_access(user, tenant_id)
    names = _names(db)
    rows = (db.query(ManualInvoice).filter(ManualInvoice.tenant_id == tenant_id, ManualInvoice.status != "draft")
            .order_by(ManualInvoice.invoice_date.desc()).all())
    return [_out(i, names) for i in rows]


@router.get("/tenants/{tenant_id}/billing/manual-invoices/{inv_id}/print")
def print_for_customer(tenant_id: str, inv_id: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    _customer_access(user, tenant_id)
    inv = _load(db, inv_id)
    if inv.tenant_id != tenant_id or (inv.status == "draft" and user.role == TENANT_ADMIN):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return mi.printable(inv, db.get(Tenant, tenant_id), _settings(db))

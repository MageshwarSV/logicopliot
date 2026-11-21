"""Online invoice payment through Razorpay (IDFC FIRST Bank's gateway) - see
app/core/razorpay_gateway.py for the flow and what each step trusts.

Only a Tenant Admin pays, and only their own organization's invoices. Bank transfer stays
available alongside (the bank details from Invoice Settings are returned for the page to show),
and SaaS Finance can still mark an invoice paid by hand for those.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.tenants import _require_billing_read_access
from app.core import razorpay_gateway as gw
from app.core.config import get_settings
from app.core.deps import get_current_user, get_db, require_role
from app.models.invoice import Invoice
from app.models.invoice_payment import InvoicePayment
from app.models.invoice_settings import SINGLETON_ID, InvoiceSettings
from app.models.user import TENANT_ADMIN, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payments"])


class PaymentOptionsOut(BaseModel):
    online_enabled: bool
    can_pay: bool
    fee_percent: float
    fee_gst_percent: float
    bank_details: str | None


class PayOrderOut(BaseModel):
    payment_id: str
    key_id: str
    order_id: str
    amount_paise: int
    currency: str
    invoice_number: str
    invoice_amount: float
    fee_amount: float
    fee_gst_amount: float
    total_amount: float
    business_name: str
    prefill_name: str
    prefill_email: str


class VerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class VerifyOut(BaseModel):
    status: str
    paid_at: str | None
    payment_reference: str | None


def _own_invoice(db: Session, user: User, tenant_id: str, invoice_id: str) -> Invoice:
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    inv = db.get(Invoice, invoice_id)
    if inv is None or inv.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return inv


@router.get("/tenants/{tenant_id}/billing/payment-options", response_model=PaymentOptionsOut)
def payment_options(tenant_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> PaymentOptionsOut:
    """What the Invoices page offers: Pay now (when the gateway is set up, Tenant Admin only),
    the gateway charge that will be added, and the bank details for a transfer instead."""
    _require_billing_read_access(user, tenant_id)
    if gw.is_configured():
        # Settle attempts we never heard back on (closed tab, no webhook yet) before the page
        # lists the invoices - recent ones only, so a page load costs at most a few lookups.
        since = datetime.now(timezone.utc) - timedelta(days=3)
        open_rows = (db.query(InvoicePayment)
                     .filter(InvoicePayment.tenant_id == tenant_id,
                             InvoicePayment.status.in_(("created", "failed")),
                             InvoicePayment.created_at >= since)
                     .order_by(InvoicePayment.created_at).limit(10).all())
        gw.reconcile(db, open_rows)
    s = get_settings()
    inv_settings = db.get(InvoiceSettings, SINGLETON_ID)
    return PaymentOptionsOut(
        online_enabled=gw.is_configured(),
        can_pay=gw.is_configured() and user.role == TENANT_ADMIN and user.tenant_id == tenant_id,
        fee_percent=s.razorpay_fee_percent,
        fee_gst_percent=s.razorpay_fee_gst_percent,
        bank_details=(inv_settings.bank_details if inv_settings else None) or None,
    )


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/pay", response_model=PayOrderOut)
def start_payment(tenant_id: str, invoice_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_role(TENANT_ADMIN))) -> PayOrderOut:
    """Create a Razorpay order for this invoice - amount worked out here, never taken from the
    browser - and return what the payment window needs."""
    inv = _own_invoice(db, user, tenant_id, invoice_id)
    if inv.status == "paid":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This invoice is already paid.")
    if inv.total_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to pay on this invoice.")
    if not gw.is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Online payment is not set up yet. Please pay by bank transfer.")
    # An earlier attempt on this invoice may have been paid without us hearing back - settle it
    # first, so the tenant is never asked to pay twice.
    earlier = (db.query(InvoicePayment)
               .filter(InvoicePayment.invoice_id == inv.id, InvoicePayment.status.in_(("created", "failed")))
               .all())
    if earlier and gw.reconcile(db, earlier):
        db.refresh(inv)
        if inv.status == "paid":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This invoice has just been confirmed as paid. Refresh the page.")
    charge = gw.compute_charge(inv.total_amount)
    try:
        order = gw.create_order(charge.amount_paise, inv.invoice_number,
                                {"tenant_id": tenant_id, "invoice_id": inv.id, "invoice_number": inv.invoice_number})
    except gw.GatewayError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    row = InvoicePayment(
        tenant_id=tenant_id, invoice_id=inv.id, razorpay_order_id=order["id"],
        invoice_amount=charge.invoice_amount, fee_amount=charge.fee_amount,
        fee_gst_amount=charge.fee_gst_amount, total_amount=charge.total_amount,
        amount_paise=charge.amount_paise, currency="INR", status="created", created_by=user.id,
    )
    db.add(row)
    db.commit()
    inv_settings = db.get(InvoiceSettings, SINGLETON_ID)
    return PayOrderOut(
        payment_id=row.id, key_id=get_settings().razorpay_key_id, order_id=order["id"],
        amount_paise=charge.amount_paise, currency="INR", invoice_number=inv.invoice_number,
        invoice_amount=charge.invoice_amount, fee_amount=charge.fee_amount,
        fee_gst_amount=charge.fee_gst_amount, total_amount=charge.total_amount,
        business_name=(inv_settings.seller_name if inv_settings and inv_settings.seller_name else "Cargora"),
        prefill_name=user.full_name or "", prefill_email=user.email or "",
    )


@router.post("/tenants/{tenant_id}/billing/invoices/{invoice_id}/pay/verify", response_model=VerifyOut)
def verify_payment(tenant_id: str, invoice_id: str, payload: VerifyIn, db: Session = Depends(get_db),
                   user: User = Depends(require_role(TENANT_ADMIN))) -> VerifyOut:
    """The payment window finished: check Razorpay's signature, then ask Razorpay what was
    actually paid (capturing it if the account is not on automatic capture), then mark paid."""
    inv = _own_invoice(db, user, tenant_id, invoice_id)
    row = (db.query(InvoicePayment)
           .filter(InvoicePayment.razorpay_order_id == payload.razorpay_order_id,
                   InvoicePayment.invoice_id == inv.id).first())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if row.status != "paid":
        if not gw.verify_checkout_signature(payload.razorpay_order_id, payload.razorpay_payment_id,
                                            payload.razorpay_signature):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The payment could not be verified. If money was taken, it will be "
                                       "confirmed automatically within a few minutes.")
        try:
            entity = gw.fetch_payment(payload.razorpay_payment_id)
            if entity.get("status") == "authorized":
                entity = gw.capture_payment(payload.razorpay_payment_id, row.amount_paise)
        except gw.GatewayError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        if not gw.apply_payment(db, row, entity):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="The payment did not match this invoice. Please contact support.")
    db.refresh(inv)
    return VerifyOut(status=inv.status, paid_at=inv.paid_at, payment_reference=row.razorpay_payment_id)


@router.post("/payments/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Razorpay calling the server directly. No login - the signature (made with the webhook
    secret) is the authentication. Exempt from the browser-header check in main.py for that
    reason. Always 200 for a correctly signed event, so Razorpay stops retrying it."""
    raw = await request.body()
    if not gw.verify_webhook_signature(raw, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    try:
        event = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid body")
    kind = event.get("event")
    entity = (((event.get("payload") or {}).get("payment") or {}).get("entity")) or {}
    order_id = entity.get("order_id")
    if not order_id:
        return {"ok": True}
    row = db.query(InvoicePayment).filter(InvoicePayment.razorpay_order_id == order_id).first()
    if row is None:
        logger.info("razorpay webhook %s for an order we did not create (%s)", kind, order_id)
        return {"ok": True}
    if kind in ("payment.captured", "order.paid"):
        gw.apply_payment(db, row, entity)
    elif kind == "payment.failed" and row.status != "paid":
        row.status = "failed"
        row.razorpay_payment_id = entity.get("id")
        row.error = (entity.get("error_description") or entity.get("error_reason") or "Payment failed")[:500]
        db.commit()
    return {"ok": True}

"""Razorpay (IDFC FIRST Bank's gateway): a Tenant Admin pays an invoice with "Pay now".

The flow, and what each step trusts:
  1. create_order   - the SERVER works out the amount (invoice total + the gateway charge passed
                      on to the tenant) and asks Razorpay for an order. The browser never sends an
                      amount, so it cannot change one.
  2. the browser opens Razorpay's own payment window for that order (UPI / card / netbanking).
  3. verify_checkout_signature - Razorpay hands the browser order id + payment id + a signature;
                      the server checks the signature with the key secret, then asks Razorpay
                      itself what was paid before believing anything.
  4. verify_webhook_signature  - Razorpay also tells the server directly (the tenant may close the
                      tab right after paying). Same outcome, applied once - see apply_payment.

Plain urllib, no SDK: three calls (create order, fetch payment, capture) are all this needs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 20


class GatewayNotConfigured(Exception):
    pass


class GatewayError(Exception):
    pass


def is_configured() -> bool:
    s = get_settings()
    return bool(s.razorpay_key_id and s.razorpay_key_secret)


# ---- amounts ---------------------------------------------------------------------------

def _money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Charge:
    invoice_amount: float
    fee_amount: float
    fee_gst_amount: float
    total_amount: float
    amount_paise: int


def compute_charge(invoice_total: float, fee_percent: float | None = None,
                   fee_gst_percent: float | None = None) -> Charge:
    """Invoice total + gateway charge (fee_percent of the total) + GST on that charge, to the
    paisa. Decimal throughout - a float sum can land one paisa off what Razorpay is asked for."""
    s = get_settings()
    pct = Decimal(str(s.razorpay_fee_percent if fee_percent is None else fee_percent))
    gst = Decimal(str(s.razorpay_fee_gst_percent if fee_gst_percent is None else fee_gst_percent))
    base = _money(Decimal(str(invoice_total)))
    fee = _money(base * pct / 100) if pct > 0 else Decimal("0.00")
    fee_gst = _money(fee * gst / 100) if fee > 0 else Decimal("0.00")
    total = base + fee + fee_gst
    return Charge(float(base), float(fee), float(fee_gst), float(total), int(total * 100))


# ---- signatures ------------------------------------------------------------------------

def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    secret = get_settings().razorpay_key_secret
    if not (secret and order_id and payment_id and signature):
        return False
    expected = _hmac_hex(secret, f"{order_id}|{payment_id}".encode("utf-8"))
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    secret = get_settings().razorpay_webhook_secret
    if not (secret and signature):
        return False
    return hmac.compare_digest(_hmac_hex(secret, raw_body), signature)


# ---- Razorpay API ----------------------------------------------------------------------

def _call(method: str, path: str, body: dict | None = None) -> dict:
    s = get_settings()
    if not is_configured():
        raise GatewayNotConfigured("Online payment is not set up on this server.")
    token = base64.b64encode(f"{s.razorpay_key_id}:{s.razorpay_key_secret}".encode()).decode()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        s.razorpay_api_base.rstrip("/") + path, data=data, method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("description", "")
        except Exception:  # noqa: BLE001
            pass
        logger.warning("razorpay %s %s failed: %s %s", method, path, exc.code, detail)
        raise GatewayError(detail or f"The payment gateway refused the request ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("razorpay %s %s unreachable: %s", method, path, exc)
        raise GatewayError("The payment gateway could not be reached. Please try again.") from exc


def create_order(amount_paise: int, receipt: str, notes: dict) -> dict:
    return _call("POST", "/orders", {"amount": amount_paise, "currency": "INR",
                                     "receipt": receipt[:40], "notes": notes})


def fetch_payment(payment_id: str) -> dict:
    return _call("GET", f"/payments/{payment_id}")


def capture_payment(payment_id: str, amount_paise: int) -> dict:
    return _call("POST", f"/payments/{payment_id}/capture", {"amount": amount_paise, "currency": "INR"})


# ---- applying a confirmed payment --------------------------------------------------------

def apply_payment(db: Session, payment_row, entity: dict) -> bool:
    """Mark the attempt and its invoice paid from a Razorpay payment entity - once. Called from
    both the browser's confirmation and the webhook; whichever comes second finds it already
    paid and does nothing. Refuses a payment that is not captured, is for another order, or is
    for any amount but the one this order was created for. True when it is (now) paid."""
    from app.models.invoice import Invoice

    if payment_row.status == "paid":
        return True
    if (entity.get("order_id") != payment_row.razorpay_order_id
            or int(entity.get("amount") or 0) != payment_row.amount_paise
            or (entity.get("currency") or "INR") != payment_row.currency
            or entity.get("status") != "captured"):
        logger.warning("razorpay payment %s does not match order %s (status %s, amount %s)",
                       entity.get("id"), payment_row.razorpay_order_id, entity.get("status"), entity.get("amount"))
        return False
    now = datetime.now(timezone.utc).isoformat()
    payment_row.status = "paid"
    payment_row.razorpay_payment_id = entity.get("id")
    payment_row.method = (entity.get("method") or "")[:20] or None
    payment_row.error = None
    payment_row.paid_at = now
    invoice = db.get(Invoice, payment_row.invoice_id)
    if invoice is not None and invoice.status != "paid":
        invoice.status = "paid"
        invoice.paid_at = now
    db.commit()
    logger.info("invoice %s paid online (razorpay %s)", payment_row.invoice_id, entity.get("id"))
    return True

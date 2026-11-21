"""Online invoice payment (Razorpay): who may pay, the amount is the server's, a payment is only
accepted when Razorpay's signature checks out and the amount matches, the webhook works without
a browser and applies a payment once."""
import hashlib
import hmac
import json

import pytest

from app.core import razorpay_gateway as gw
from app.core.config import get_settings
from app.models.invoice import Invoice
from app.models.invoice_payment import InvoicePayment
from app.models.invoice_settings import SINGLETON_ID, InvoiceSettings
from tests.conftest import login, make_tenant, make_user

KEY_SECRET = "test_key_secret"
HOOK_SECRET = "test_webhook_secret"


def _sign(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


@pytest.fixture()
def gateway(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_key_id", "rzp_test_abc")
    monkeypatch.setattr(s, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(s, "razorpay_webhook_secret", HOOK_SECRET)
    monkeypatch.setattr(s, "razorpay_fee_percent", 2.0)
    monkeypatch.setattr(s, "razorpay_fee_gst_percent", 18.0)
    calls = {"orders": [], "captures": []}
    payments: dict[str, dict] = {}

    def create_order(amount_paise, receipt, notes):
        calls["orders"].append(amount_paise)
        return {"id": f"order_{len(calls['orders'])}", "amount": amount_paise}

    def fetch_payment(pid):
        return payments[pid]

    def capture_payment(pid, amount):
        calls["captures"].append((pid, amount))
        payments[pid] = {**payments[pid], "status": "captured"}
        return payments[pid]

    monkeypatch.setattr(gw, "create_order", create_order)
    monkeypatch.setattr(gw, "fetch_payment", fetch_payment)
    monkeypatch.setattr(gw, "capture_payment", capture_payment)
    return {"calls": calls, "payments": payments}


def _invoice(db, tenant, total=1000.0, status="unpaid", number="INV-2026-10-001"):
    inv = Invoice(
        tenant_id=tenant.id, invoice_number=number, period_start="2026-10-01", period_end="2026-10-31",
        platform_fee=0, platform_fee_billed_days=31, platform_fee_month_days=31, total_jobs=1,
        total_pages=10, tier1_rate=1, tier1_pages=10, tier1_amount=10, tier2_rate=0, tier2_pages=0,
        tier2_amount=0, usage_charges=10, subtotal=total, gst_percent=0, gst_amount=0,
        total_amount=total, by_shipment_type={}, status=status,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def _tenant_admin(client, db, tenant, email="ta-pay@example.com"):
    ta = make_user(db, role="tenant_admin", tenant=tenant, email=email)
    login(client, ta.email)
    return ta


def _start(client, tenant, inv):
    return client.post(f"/api/v1/tenants/{tenant.id}/billing/invoices/{inv.id}/pay")


def test_charge_adds_the_gateway_fee_and_gst_on_it_to_the_paisa():
    c = gw.compute_charge(1000.0, 2.0, 18.0)
    assert (c.fee_amount, c.fee_gst_amount, c.total_amount, c.amount_paise) == (20.0, 3.6, 1023.6, 102360)
    c = gw.compute_charge(1234.57, 0.0, 18.0)
    assert (c.fee_amount, c.total_amount, c.amount_paise) == (0.0, 1234.57, 123457)


def test_options_offer_pay_now_only_to_the_tenant_admin_when_set_up(client, db_session, gateway):
    tenant = make_tenant(db_session)
    db_session.add(InvoiceSettings(id=SINGLETON_ID, bank_details="HDFC 1234"))
    db_session.commit()
    _tenant_admin(client, db_session, tenant)
    body = client.get(f"/api/v1/tenants/{tenant.id}/billing/payment-options").json()
    assert body["online_enabled"] and body["can_pay"] and body["bank_details"] == "HDFC 1234"
    assert body["fee_percent"] == 2.0

    sa = make_user(db_session, role="super_admin", email="sa-pay@example.com")
    login(client, sa.email)
    body = client.get(f"/api/v1/tenants/{tenant.id}/billing/payment-options").json()
    assert body["online_enabled"] and not body["can_pay"]


def test_options_without_keys_offer_bank_transfer_only(client, db_session):
    tenant = make_tenant(db_session)
    _tenant_admin(client, db_session, tenant)
    body = client.get(f"/api/v1/tenants/{tenant.id}/billing/payment-options").json()
    assert not body["online_enabled"] and not body["can_pay"]


def test_start_payment_uses_the_servers_amount(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    resp = _start(client, tenant, inv)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["amount_paise"] == 102360 and body["key_id"] == "rzp_test_abc"
    assert gateway["calls"]["orders"] == [102360]
    row = db_session.query(InvoicePayment).one()
    assert row.status == "created" and row.razorpay_order_id == body["order_id"]


def test_only_the_tenants_own_admin_can_start_a_payment(client, db_session, gateway):
    tenant = make_tenant(db_session)
    other = make_tenant(db_session, name="Other")
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, other, email="ta-other@example.com")
    assert _start(client, tenant, inv).status_code == 404
    sa = make_user(db_session, role="super_admin", email="sa-pay2@example.com")
    login(client, sa.email)
    assert _start(client, tenant, inv).status_code == 403


def test_a_paid_invoice_cannot_be_paid_again(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant, status="paid")
    _tenant_admin(client, db_session, tenant)
    assert _start(client, tenant, inv).status_code == 409


def test_without_keys_starting_a_payment_says_use_bank_transfer(client, db_session):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    resp = _start(client, tenant, inv)
    assert resp.status_code == 503 and "bank transfer" in resp.json()["detail"]


def _verify(client, tenant, inv, order_id, pid, signature=None):
    sig = signature or _sign(KEY_SECRET, f"{order_id}|{pid}".encode())
    return client.post(f"/api/v1/tenants/{tenant.id}/billing/invoices/{inv.id}/pay/verify",
                       json={"razorpay_order_id": order_id, "razorpay_payment_id": pid, "razorpay_signature": sig})


def test_verified_payment_marks_the_invoice_paid(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    gateway["payments"]["pay_1"] = {"id": "pay_1", "order_id": order_id, "amount": 102360,
                                    "currency": "INR", "status": "captured", "method": "upi"}
    resp = _verify(client, tenant, inv, order_id, "pay_1")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paid" and resp.json()["payment_reference"] == "pay_1"
    db_session.refresh(inv)
    assert inv.status == "paid" and inv.paid_at
    listed = client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices").json()
    assert listed[0]["payment_reference"] == "pay_1"


def test_an_authorized_payment_is_captured_first(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    gateway["payments"]["pay_2"] = {"id": "pay_2", "order_id": order_id, "amount": 102360,
                                    "currency": "INR", "status": "authorized"}
    assert _verify(client, tenant, inv, order_id, "pay_2").status_code == 200
    assert gateway["calls"]["captures"] == [("pay_2", 102360)]


def test_a_bad_signature_is_refused(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    gateway["payments"]["pay_3"] = {"id": "pay_3", "order_id": order_id, "amount": 102360,
                                    "currency": "INR", "status": "captured"}
    assert _verify(client, tenant, inv, order_id, "pay_3", signature="forged").status_code == 400
    db_session.refresh(inv)
    assert inv.status == "unpaid"


def test_a_payment_for_a_different_amount_is_not_accepted(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    gateway["payments"]["pay_4"] = {"id": "pay_4", "order_id": order_id, "amount": 100,
                                    "currency": "INR", "status": "captured"}
    assert _verify(client, tenant, inv, order_id, "pay_4").status_code == 409
    db_session.refresh(inv)
    assert inv.status == "unpaid"


def _hook(client, event: dict, secret=HOOK_SECRET):
    raw = json.dumps(event).encode()
    # No X-Requested-With: Razorpay's server never sends it.
    return client.post("/api/v1/payments/razorpay/webhook", content=raw,
                       headers={"x-razorpay-signature": _sign(secret, raw)})


def test_webhook_marks_paid_once_and_needs_no_browser_header(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    client.headers.pop("X-Requested-With", None)
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_5", "order_id": order_id, "amount": 102360, "currency": "INR",
        "status": "captured", "method": "card"}}}}
    assert _hook(client, event).status_code == 200
    assert _hook(client, event).status_code == 200  # Razorpay retries: still applied once
    db_session.refresh(inv)
    assert inv.status == "paid"
    row = db_session.query(InvoicePayment).one()
    assert row.status == "paid" and row.method == "card" and row.razorpay_payment_id == "pay_5"


def test_webhook_with_a_bad_signature_is_refused(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_6", "order_id": order_id, "amount": 102360, "currency": "INR", "status": "captured"}}}}
    assert _hook(client, event, secret="wrong").status_code == 400
    db_session.refresh(inv)
    assert inv.status == "unpaid"


def test_webhook_failed_payment_is_recorded_and_unknown_orders_ignored(client, db_session, gateway):
    tenant = make_tenant(db_session)
    inv = _invoice(db_session, tenant)
    _tenant_admin(client, db_session, tenant)
    order_id = _start(client, tenant, inv).json()["order_id"]
    failed = {"event": "payment.failed", "payload": {"payment": {"entity": {
        "id": "pay_7", "order_id": order_id, "amount": 102360, "status": "failed",
        "error_description": "Bank declined"}}}}
    assert _hook(client, failed).status_code == 200
    row = db_session.query(InvoicePayment).one()
    db_session.refresh(row)
    assert row.status == "failed" and row.error == "Bank declined"
    unknown = {"event": "payment.captured", "payload": {"payment": {"entity": {"order_id": "order_x"}}}}
    assert _hook(client, unknown).status_code == 200

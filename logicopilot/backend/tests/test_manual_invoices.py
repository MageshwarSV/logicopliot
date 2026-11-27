"""Manual invoices: draft -> issue (shared number series) -> paid / cancelled; customer view."""
import uuid
from datetime import date, timedelta

from app.models.invoice import Invoice
from app.models.user import SAAS_FINANCE, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

URL = "/api/v1/finance/manual-invoices"


def _e(p):
    return f"{p}-{uuid.uuid4().hex[:8]}@example.com"


def _setup(client, db):
    t = make_tenant(db, name=f"4S-{uuid.uuid4().hex[:5]}", billing_state_code="33")
    fin = make_user(db, role=SAAS_FINANCE, email=_e("fin"))
    assert login(client, fin.email).status_code == 200
    return t


def _draft(client, t, **kw):
    body = {"tenant_id": t.id, "invoice_date": "2026-11-27", "period_label": "November 2026",
            "lines": [{"description": "Separate ERP server - November 2026", "sac": "998316", "qty": 1, "rate": 10000}],
            "gst_percent": 18, **kw}
    return client.post(URL, json=body)


def test_draft_totals_and_default_due_date(client, db_session):
    t = _setup(client, db_session)
    r = _draft(client, t)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "draft" and d["invoice_number"] is None
    assert (d["subtotal"], d["gst_amount"], d["total_amount"]) == (10000, 1800, 11800)
    assert d["due_date"] == (date(2026, 11, 27) + timedelta(days=7)).isoformat()
    # same-day due date allowed; due before invoice date refused
    assert _draft(client, t, due_date="2026-11-27").status_code == 200
    assert _draft(client, t, due_date="2026-11-20").status_code == 422
    assert _draft(client, t, lines=[{"description": "", "qty": 1, "rate": 5}]).status_code == 422


def test_issue_takes_the_next_number_after_usage_invoices(client, db_session):
    t = _setup(client, db_session)
    db_session.add(Invoice(tenant_id=t.id, invoice_number="INV/2026-27/0004", financial_year="2026-27", fy_sequence=4,
                           period_start="2026-10-01", period_end="2026-10-31", platform_fee=0, platform_fee_billed_days=0,
                           platform_fee_month_days=31, total_jobs=0, total_pages=0, tier1_rate=0, tier1_pages=0,
                           tier1_amount=0, tier2_rate=0, tier2_pages=0, tier2_amount=0, usage_charges=0, subtotal=0,
                           gst_percent=18, gst_amount=0, total_amount=0, by_shipment_type={}))
    db_session.commit()
    d = _draft(client, t).json()
    r = client.post(f"{URL}/{d['id']}/issue")
    assert r.status_code == 200 and r.json()["invoice_number"] == "INV/2026-27/0005"
    # locked once issued
    assert client.put(f"{URL}/{d['id']}", json={"tenant_id": t.id, "lines": []}).status_code == 409
    assert client.delete(f"{URL}/{d['id']}").status_code == 409
    p = client.get(f"{URL}/{d['id']}/print").json()
    assert p["invoice_number"] == "INV/2026-27/0005" and p["lines"][0]["sac"] == "998316" and p["total"] == 11800


def test_paid_unpaid_cancel_and_customer_view(client, db_session):
    t = _setup(client, db_session)
    draft = _draft(client, t).json()
    issued = _draft(client, t).json()
    client.post(f"{URL}/{issued['id']}/issue")
    assert client.post(f"{URL}/{issued['id']}/mark-paid", json={"paid": True}).json()["status"] == "paid"
    assert client.post(f"{URL}/{issued['id']}/mark-paid", json={"paid": False}).json()["status"] == "issued"
    assert client.post(f"{URL}/{issued['id']}/cancel", json={"reason": ""}).status_code == 422
    c = client.post(f"{URL}/{issued['id']}/cancel", json={"reason": "Wrong amount"}).json()
    assert c["status"] == "cancelled" and c["invoice_number"]           # number kept
    assert client.post(f"{URL}/{draft['id']}/cancel", json={"reason": "x"}).status_code == 409
    assert client.delete(f"{URL}/{draft['id']}").status_code == 200
    # the customer sees the cancelled one, never a draft
    ta = make_user(db_session, role=TENANT_ADMIN, tenant=t, email=_e("ta"))
    assert login(client, ta.email).status_code == 200
    mine = client.get(f"/api/v1/tenants/{t.id}/billing/manual-invoices").json()
    assert [m["status"] for m in mine] == ["cancelled"]
    assert client.get(URL).status_code == 403

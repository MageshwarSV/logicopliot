"""SaaS Finance business analytics: figures match the invoices, manual invoices count as
'Other services', the days add up to the month, both product views."""
import uuid
from datetime import date, datetime, timedelta, timezone

from app.core import finance_analytics as fa
from app.models.billable_page_event import BillablePageEvent
from app.models.invoice import Invoice
from app.models.manual_invoice import ManualInvoice
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import login, make_tenant, make_user

IST = timezone(timedelta(hours=5, minutes=30))


def _world(db):
    today = fa.today_ist()
    cur = today.replace(day=1)
    prev = fa.add_months(cur, -1)
    t = make_tenant(db, name=f"4S-{uuid.uuid4().hex[:4]}")
    cfg = TenantBillingConfig(tenant_id=t.id, billing_start_date=fa.add_months(cur, -2).isoformat())
    db.add(cfg)
    db.flush()
    cfg.created_at = datetime(fa.add_months(cur, -2).year, fa.add_months(cur, -2).month, 1, tzinfo=timezone.utc)
    # last month: invoiced 1000 platform + 2000 usage on 800 pages (600 air import, 200 sea import)
    db.add(Invoice(tenant_id=t.id, invoice_number=f"INV/X/{uuid.uuid4().hex[:4]}", financial_year="x", fy_sequence=1,
                   period_start=prev.isoformat(), period_end=(cur - timedelta(days=1)).isoformat(), platform_fee=1000,
                   platform_fee_billed_days=30, platform_fee_month_days=30, total_jobs=10, total_pages=800,
                   tier1_rate=2.5, tier1_pages=800, tier1_amount=2000, tier2_rate=2, tier2_pages=0, tier2_amount=0,
                   usage_charges=2000, subtotal=3000, gst_percent=18, gst_amount=540, total_amount=3540, by_shipment_type={}))
    for day, stype, pages in ((3, "Air Import", 600), (10, "Sea Import", 200)):
        db.add(BillablePageEvent(tenant_id=t.id, job_id=uuid.uuid4().hex, job_reference="J", shipment_type=stype, page_count=pages,
                                 created_at=datetime(prev.year, prev.month, day, 12, tzinfo=IST)))
    # a manual invoice last month (counts) and a cancelled one (does not)
    db.add(ManualInvoice(tenant_id=t.id, status="issued", invoice_date=prev.replace(day=5).isoformat(),
                         due_date=prev.replace(day=12).isoformat(), lines=[], subtotal=5000, gst_amount=900, total_amount=5900))
    db.add(ManualInvoice(tenant_id=t.id, status="cancelled", invoice_date=prev.replace(day=6).isoformat(),
                         due_date=prev.replace(day=12).isoformat(), lines=[], subtotal=9999, total_amount=9999))
    db.commit()
    return t, cur, prev


def test_last_month_matches_invoice_plus_manual(db_session):
    t, cur, prev = _world(db_session)
    lg = fa.Ledger(db_session)
    tot = lg.totals(prev)
    assert (tot["platform"], tot["usage"], tot["other"], tot["revenue"]) == (1000, 2000, 5000, 8000)
    ship = lg.by_shipment(prev)
    assert ship["Air Import"] == {"pages": 600, "revenue": 1500.0} and ship["Sea Import"] == {"pages": 200, "revenue": 500.0}


def test_daily_adds_up_to_the_month_usage(db_session):
    t, cur, prev = _world(db_session)
    d = fa.daily(db_session, prev)
    assert round(sum(x["revenue"] or 0 for x in d["days"]), 2) == 2000
    assert d["days"][2]["pages"] == 600 and d["days"][2]["Air Import"] == 600 and d["days"][9]["revenue"] == 500


def test_endpoints_for_finance(client, db_session):
    t, cur, prev = _world(db_session)
    fin = make_user(db_session, role="saas_finance", email=f"fin-{uuid.uuid4().hex[:6]}@example.com")
    assert login(client, fin.email).status_code == 200
    ov = client.get("/api/v1/finance/analytics/overview").json()
    assert ov["kpis"]["revenue_prev_month"] == 8000 and ov["kpis"]["arr"] == 96000
    assert any(m["key"] == "first_customer" for m in ov["milestones"])
    fy = fa.fy_start_year(prev)
    mon = client.get("/api/v1/finance/analytics/monthly", params={"fy": fy}).json()
    row = next(r for r in mon["months"] if r["month"] == prev.strftime("%Y-%m"))
    assert row["revenue"] == 8000 and len(mon["months"]) == 12 and len(mon["last_year"]) == 12
    cust = client.get("/api/v1/finance/analytics/customers", params={"month": prev.strftime("%Y-%m")}).json()
    assert cust["rows"][0]["revenue"] == 8000 and cust["rows"][0]["share_pct"] == 100
    prod = client.get("/api/v1/finance/analytics/products", params={"fy": fy}).json()
    assert {r["key"]: r["revenue"] for r in prod["by_revenue_type"]}["Other services"] >= 5000
    assert client.get("/api/v1/finance/analytics/daily", params={"month": "2026-13"}).status_code == 422

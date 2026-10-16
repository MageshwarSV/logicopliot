"""Billing & Usage: current-month live usage, past-month auto-finalized invoices, and the
Super-Admin-only mark-as-paid action. See app/core/billing.py for the aggregation/finalization
logic this exercises."""
import calendar
from datetime import datetime, timedelta, timezone

from app.models.billable_page_event import BillablePageEvent
from app.models.invoice import Invoice
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-bill@example.com")
    login(client, sa.email)
    return sa


def _make_group(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_template_document(db_session, tenant, group, name="BL"):
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name, doc_type="BL")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _make_job_with_pages(db_session, tenant, group, template_doc, *, pages, created_at, status="completed"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-BILL", status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    job.created_at = created_at
    doc = JobDocument(
        tenant_id=tenant.id, job_id=job.id, template_document_id=template_doc.id,
        file_path="/tmp/fake.pdf", page_count=pages,
    )
    db_session.add(doc)
    # compute_usage sums pages from this permanent ledger, not JobDocument.page_count (which a
    # delete can zero out or remove) - mirror app/core/billing.py's record_billable_pages here,
    # dated at the SAME moment the job itself "happened" so existing period-based assertions
    # keep meaning what they did before the ledger existed.
    if pages:
        event = BillablePageEvent(
            tenant_id=tenant.id, job_id=job.id, job_reference=job.reference,
            shipment_type=group.mode or "Unassigned", page_count=pages,
        )
        db_session.add(event)
        db_session.commit()
        event.created_at = created_at
    db_session.commit()
    return job


def _save_billing_config(db_session, tenant, *, created_at=None, **overrides):
    config = TenantBillingConfig(tenant_id=tenant.id, **overrides)
    db_session.add(config)
    db_session.commit()
    if created_at is not None:
        config.created_at = created_at
        db_session.commit()
    db_session.refresh(config)
    return config


def test_current_month_usage_is_live_and_not_finalized(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=now)
    _save_billing_config(db_session, tenant, created_at=now - timedelta(days=5))

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_finalized"] is False
    assert body["total_jobs"] == 1
    assert body["total_pages"] == 10
    assert body["by_shipment_type"] == [{"shipment_type": "Sea Import", "jobs": 1, "pages": 10}]
    assert db_session.query(Invoice).count() == 0  # current month never finalized


def test_billable_pages_count_regardless_of_job_status(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=5, created_at=now, status="failed")
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=3, created_at=now, status="duplicate")
    _save_billing_config(db_session, tenant, created_at=now - timedelta(days=5))

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_pages"] == 8


def test_past_month_auto_finalizes_into_an_invoice(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)

    now = datetime.now(timezone.utc)
    first_of_this_month = now.replace(day=1)
    last_month_day = first_of_this_month - timedelta(days=1)
    last_month_start = last_month_day.replace(day=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=100, created_at=last_month_day)
    _save_billing_config(
        db_session, tenant, created_at=last_month_start,
        cargora_monthly_fee=4999.0, cargora_tier1_rate_per_page=2.5, cargora_tier1_page_limit=25000,
        cargora_tier2_rate_per_page=2.0, gst_percent=18.0,
    )

    month_str = last_month_start.strftime("%Y-%m")
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage", params={"month": month_str})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_finalized"] is True
    assert body["status"] == "unpaid"
    assert body["total_pages"] == 100
    assert body["platform_fee"] == 4999.0
    assert body["tier1_amount"] == 250.0  # 100 pages * 2.5
    assert body["usage_charges"] == 250.0
    assert body["subtotal"] == 5249.0
    assert body["gst_amount"] == round(5249.0 * 0.18, 2)
    assert body["total_amount"] == round(5249.0 * 1.18, 2)

    invoices = db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
    assert len(invoices) == 1
    assert invoices[0].status == "unpaid"


def test_finalizing_is_idempotent_across_repeated_access(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1))

    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    assert db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).count() == 1


def test_no_invoices_finalized_before_billing_was_ever_configured(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    # No TenantBillingConfig row saved at all.

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    assert db_session.query(Invoice).count() == 0


def test_super_admin_can_mark_an_invoice_paid(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1))

    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    invoice = db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).first()

    resp = client.post(f"/api/v1/tenants/{tenant.id}/billing/invoices/{invoice.id}/mark-paid", json={"paid": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "paid"
    assert body["paid_at"] is not None


def test_tenant_admin_cannot_mark_an_invoice_paid(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1))
    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    invoice = db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).first()

    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-bill@example.com")
    login(client, ta.email)
    resp = client.post(f"/api/v1/tenants/{tenant.id}/billing/invoices/{invoice.id}/mark-paid", json={"paid": True})
    assert resp.status_code == 403


def test_tenant_admin_can_view_their_own_tenants_billing(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=now)
    _save_billing_config(db_session, tenant, created_at=now - timedelta(days=5))

    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-bill2@example.com")
    login(client, ta.email)
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_pages"] == 10


def test_tenant_admin_cannot_view_another_tenants_billing(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    ta = make_user(db_session, role="tenant_admin", tenant=tenant_a, email="ta-bill3@example.com")
    login(client, ta.email)

    resp = client.get(f"/api/v1/tenants/{tenant_b.id}/billing/usage")
    assert resp.status_code == 404


def test_platform_fee_is_prorated_in_the_current_live_month(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=now)

    month_days = calendar.monthrange(now.year, now.month)[1]
    go_live_day = min(5, month_days)
    billing_start = now.replace(day=go_live_day).strftime("%Y-%m-%d")
    expected_billed_days = month_days - go_live_day + 1
    _save_billing_config(
        db_session, tenant, created_at=now - timedelta(days=60),
        billing_start_date=billing_start, cargora_monthly_fee=4999.0,
    )

    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform_fee_billed_days"] == expected_billed_days
    assert body["platform_fee_month_days"] == month_days
    assert body["platform_fee"] == round(4999.0 * expected_billed_days / month_days, 2)


def test_platform_fee_bills_in_full_for_a_past_invoice_with_no_proration(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1), cargora_monthly_fee=4999.0)

    month_str = last_month_day.replace(day=1).strftime("%Y-%m")
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage", params={"month": month_str})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform_fee_billed_days"] == body["platform_fee_month_days"]
    assert body["platform_fee"] == 4999.0


def test_past_month_invoice_shows_prorated_platform_fee_for_the_go_live_month(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)

    now = datetime.now(timezone.utc)
    last_month_start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    last_month_days = calendar.monthrange(last_month_start.year, last_month_start.month)[1]
    go_live_day = min(10, last_month_days)
    billing_start = last_month_start.replace(day=go_live_day)
    job_created_at = last_month_start.replace(day=min(go_live_day + 1, last_month_days))

    _make_job_with_pages(db_session, tenant, group, tdoc, pages=50, created_at=job_created_at)
    _save_billing_config(
        db_session, tenant, created_at=billing_start - timedelta(days=5),
        billing_start_date=billing_start.strftime("%Y-%m-%d"), cargora_monthly_fee=4999.0,
    )

    month_str = last_month_start.strftime("%Y-%m")
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage", params={"month": month_str})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_billed_days = last_month_days - go_live_day + 1
    assert body["platform_fee_billed_days"] == expected_billed_days
    assert body["platform_fee_month_days"] == last_month_days
    assert body["platform_fee"] == round(4999.0 * expected_billed_days / last_month_days, 2)

    invoice = db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).first()
    assert invoice.platform_fee_billed_days == expected_billed_days


def test_no_invoice_finalized_for_a_month_before_go_live(client, db_session):
    tenant = make_tenant(db_session)
    _login_super_admin(client, db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)

    now = datetime.now(timezone.utc)
    two_months_ago_start = (now.replace(day=1) - timedelta(days=32)).replace(day=1)
    last_month_start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=two_months_ago_start)
    _save_billing_config(
        db_session, tenant, created_at=two_months_ago_start - timedelta(days=10),
        billing_start_date=last_month_start.date().isoformat(),
    )

    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    periods = {
        inv.period_start for inv in db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).all()
    }
    assert two_months_ago_start.date().isoformat() not in periods
    assert last_month_start.date().isoformat() in periods

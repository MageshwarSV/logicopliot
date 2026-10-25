"""GET /finance/dashboard - the one place in this whole feature with genuinely new
arithmetic (summing ACROSS tenants). The per-tenant math itself (calculate_bill) is already
covered by test_billing_usage.py; this only checks the cross-tenant sum is correct."""
from datetime import datetime, timedelta, timezone

from app.models.billable_page_event import BillablePageEvent
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import login, make_tenant, make_user


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


def _make_job_with_pages(db_session, tenant, group, template_doc, *, pages, created_at):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-FINDASH", status="completed")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    job.created_at = created_at
    doc = JobDocument(
        tenant_id=tenant.id, job_id=job.id, template_document_id=template_doc.id,
        file_path="/tmp/fake.pdf", page_count=pages,
    )
    db_session.add(doc)
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


def test_dashboard_totals_equal_the_hand_summed_tenant_figures(client, db_session):
    now = datetime.now(timezone.utc)

    t1 = make_tenant(db_session, name="Tenant Alpha")
    g1 = _make_group(db_session, t1)
    d1 = _make_template_document(db_session, t1, g1)
    _make_job_with_pages(db_session, t1, g1, d1, pages=100, created_at=now)
    _save_billing_config(db_session, t1, created_at=now - timedelta(days=10))

    t2 = make_tenant(db_session, name="Tenant Beta")
    g2 = _make_group(db_session, t2)
    d2 = _make_template_document(db_session, t2, g2)
    _make_job_with_pages(db_session, t2, g2, d2, pages=50, created_at=now)
    _save_billing_config(db_session, t2, created_at=now - timedelta(days=10))

    fin = make_user(db_session, role="saas_finance", email="fin-dash1@example.com")
    login(client, fin.email)

    resp = client.get("/api/v1/finance/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_customers"] == 2
    assert body["total_jobs"] == 2
    assert body["total_pages"] == 150
    # Both tenants use the default rate (2.50/page, well under the 25000-page tier1 limit).
    assert body["total_usage_charges"] == 150 * 2.50

    by_name = {row["tenant_name"]: row for row in body["by_customer"]}
    assert by_name["Tenant Alpha"]["pages"] == 100
    assert by_name["Tenant Beta"]["pages"] == 50
    assert by_name["Tenant Alpha"]["total_amount"] + by_name["Tenant Beta"]["total_amount"] == \
        body["total_billable_amount"]


def test_dashboard_includes_inactive_tenants_historical_usage(client, db_session):
    """A tenant deactivated after generating real usage must not have that usage silently
    disappear from the platform-wide figures - the revenue was already earned."""
    now = datetime.now(timezone.utc)
    tenant = make_tenant(db_session, name="Deactivated Tenant", is_active=False)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=20, created_at=now)
    _save_billing_config(db_session, tenant, created_at=now - timedelta(days=5))

    fin = make_user(db_session, role="saas_finance", email="fin-dash2@example.com")
    login(client, fin.email)

    resp = client.get("/api/v1/finance/dashboard")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["by_customer"] if r["tenant_name"] == "Deactivated Tenant")
    assert row["is_active"] is False
    assert row["pages"] == 20


def test_outstanding_amount_is_independent_of_selected_month(client, db_session):
    now = datetime.now(timezone.utc)
    two_months_ago = (now.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=30, created_at=two_months_ago)
    _save_billing_config(db_session, tenant, created_at=two_months_ago.replace(day=1))

    fin = make_user(db_session, role="saas_finance", email="fin-dash3@example.com")
    login(client, fin.email)
    # Finalize the old unpaid invoice by viewing the current month's dashboard first.
    client.get("/api/v1/finance/dashboard")

    resp_current = client.get("/api/v1/finance/dashboard")
    resp_old_month = client.get(f"/api/v1/finance/dashboard?month={two_months_ago:%Y-%m}")
    assert resp_current.status_code == resp_old_month.status_code == 200
    assert resp_current.json()["outstanding_amount"] == resp_old_month.json()["outstanding_amount"]
    assert resp_current.json()["outstanding_amount"] > 0


def test_tenant_admin_cannot_view_the_finance_dashboard(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-dash1@example.com")
    login(client, ta.email)
    resp = client.get("/api/v1/finance/dashboard")
    assert resp.status_code == 403

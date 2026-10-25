"""SaaS Finance gets read access to every tenant's billing/usage/invoices/pricing-config,
exactly like Super Admin - but the ONE write endpoint on pricing (updating it) must stay
Super-Admin-only. That negative case is the most important test in this file: it's the one
place a careless copy of the role tuple would accidentally grant write access."""
from datetime import datetime, timedelta, timezone

from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.tenant_billing_config import TenantBillingConfig
from tests.conftest import login, make_tenant, make_user


def _save_billing_config(db_session, tenant, *, created_at=None, **overrides):
    config = TenantBillingConfig(tenant_id=tenant.id, **overrides)
    db_session.add(config)
    db_session.commit()
    if created_at is not None:
        config.created_at = created_at
        db_session.commit()
    db_session.refresh(config)
    return config


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
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-FIN", status="completed")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    job.created_at = created_at
    doc = JobDocument(
        tenant_id=tenant.id, job_id=job.id, template_document_id=template_doc.id,
        file_path="/tmp/fake.pdf", page_count=pages,
    )
    db_session.add(doc)
    db_session.commit()
    return job


def test_saas_finance_can_view_billing_usage_for_any_tenant(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=now)

    fin = make_user(db_session, role="saas_finance", email="fin-bill1@example.com")
    login(client, fin.email)
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/usage")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_jobs"] == 1


def test_saas_finance_can_view_invoice_history_for_any_tenant(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1))

    fin = make_user(db_session, role="saas_finance", email="fin-bill2@example.com")
    login(client, fin.email)
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


def test_saas_finance_can_view_pricing_config(client, db_session):
    tenant = make_tenant(db_session)
    fin = make_user(db_session, role="saas_finance", email="fin-pricing1@example.com")
    login(client, fin.email)
    resp = client.get(f"/api/v1/tenants/{tenant.id}/billing-config")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cargora_monthly_fee"] == 4999.0


def test_saas_finance_cannot_edit_pricing_config(client, db_session):
    """The critical negative test: Finance is view-only on pricing. If this ever starts
    passing with a 200, the role tuple on update_tenant_billing_config was copied wrong."""
    tenant = make_tenant(db_session)
    fin = make_user(db_session, role="saas_finance", email="fin-pricing2@example.com")
    login(client, fin.email)
    resp = client.put(f"/api/v1/tenants/{tenant.id}/billing-config", json={"cargora_monthly_fee": 1.0})
    assert resp.status_code == 403

    config = db_session.query(TenantBillingConfig).filter(
        TenantBillingConfig.tenant_id == tenant.id).first()
    assert config is None  # nothing was ever written


def test_saas_finance_cannot_create_tenants(client, db_session):
    fin = make_user(db_session, role="saas_finance", email="fin-notenant@example.com")
    login(client, fin.email)
    resp = client.post("/api/v1/tenants", json={"name": "Sneaky New Tenant"})
    assert resp.status_code == 403

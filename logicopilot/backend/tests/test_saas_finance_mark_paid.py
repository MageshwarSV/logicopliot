"""SaaS Finance's one write action: marking an invoice paid, extended onto the existing
mark-paid endpoint (app/api/v1/tenants.py::mark_invoice_paid) rather than a new Payment
model - see test_billing_usage.py's own super_admin-flavoured version of this same test."""
from datetime import datetime, timedelta, timezone

from app.models.invoice import Invoice
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
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-FINPAY", status="completed")
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


def test_saas_finance_can_mark_an_invoice_paid(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = _make_template_document(db_session, tenant, group)
    now = datetime.now(timezone.utc)
    last_month_day = now.replace(day=1) - timedelta(days=1)
    _make_job_with_pages(db_session, tenant, group, tdoc, pages=10, created_at=last_month_day)
    _save_billing_config(db_session, tenant, created_at=last_month_day.replace(day=1))

    fin = make_user(db_session, role="saas_finance", email="fin-markpaid1@example.com")
    login(client, fin.email)
    client.get(f"/api/v1/tenants/{tenant.id}/billing/invoices")  # finalizes last month
    invoice = db_session.query(Invoice).filter(Invoice.tenant_id == tenant.id).first()
    assert invoice is not None

    resp = client.post(
        f"/api/v1/tenants/{tenant.id}/billing/invoices/{invoice.id}/mark-paid",
        json={"paid": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "paid"
    assert body["paid_at"] is not None

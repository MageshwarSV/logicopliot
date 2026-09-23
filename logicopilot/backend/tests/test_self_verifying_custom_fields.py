"""Some custom fields ARE their own cross-document check - their AI prompt already reads
several documents and reconciles them itself, returning a verdict shaped "MATCH - ...",
"MISMATCH - ..." or "CANNOT VERIFY - ..." (Air Import's "Quantity Verification", "Amount
Verification"), or a plain computed total worth showing for context (a "... (Calculated)"
field, e.g. "Total Amount (Calculated)"). _self_verifying_findings surfaces these on the same
Cross Docs Verification screen and the same _job_stage gate as mark-to-mark CrossDocLink
findings, detected from the VALUE's own shape rather than a hardcoded field name."""
from app.api.v1.jobs import _job_stage, _self_verifying_findings
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kw):
    kw.setdefault("reference", "JOB-SELFVERIFY")
    kw.setdefault("status", "extracted")
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_field(db_session, tenant, group, label):
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="ai",
                     ai_prompt="reconcile something")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return cf


def _set_value(db_session, tenant, job, cf, value):
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name, extracted_value=value))
    db_session.commit()


def test_a_match_verdict_is_a_match_status(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Amount Verification")
    _set_value(db_session, tenant, job, cf, "MATCH - lines 6000.00 = sub total 6000.00 = total 6000.00")

    findings = _self_verifying_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"
    assert findings[0]["value"].startswith("MATCH")


def test_a_mismatch_verdict_is_a_mismatch_status(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Quantity Verification")
    _set_value(db_session, tenant, job, cf, "MISMATCH - invoice 50000 vs weight list 45000 (difference 5000)")

    findings = _self_verifying_findings(db_session, job)
    assert findings[0]["status"] == "mismatch"


def test_cannot_verify_is_a_review_status(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Amount Verification")
    _set_value(db_session, tenant, job, cf, "CANNOT VERIFY - no readable amounts")

    findings = _self_verifying_findings(db_session, job)
    assert findings[0]["status"] == "review"


def test_a_calculated_total_is_informational_and_never_blocks(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Total Amount (Calculated)")
    _set_value(db_session, tenant, job, cf, "Distinct Invoices: 1\n\nTotal: 1,000.00 USD")

    findings = _self_verifying_findings(db_session, job)
    assert findings[0]["status"] == "match"


def test_a_missing_value_is_a_missing_status(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _make_field(db_session, tenant, group, "Quantity Verification")
    # No JobFieldValue at all - never computed.

    findings = _self_verifying_findings(db_session, job)
    assert findings[0]["status"] == "missing"


def test_an_ordinary_custom_field_is_not_picked_up(db_session):
    """Only fields whose LABEL says "verification" or "(calculated)" are candidates at all -
    an unrelated AI field (e.g. "Consignee") must never show up here."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Consignee")
    _set_value(db_session, tenant, job, cf, "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")

    assert _self_verifying_findings(db_session, job) == []


def test_job_stage_blocks_on_an_unaccepted_self_verifying_mismatch(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Quantity Verification")
    _set_value(db_session, tenant, job, cf, "MISMATCH - invoice 50000 vs weight list 45000 (difference 5000)")

    assert _job_stage(db_session, job) == "Data Validation"


def test_job_stage_is_not_blocked_once_the_mismatch_is_accepted(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Quantity Verification")
    _set_value(db_session, tenant, job, cf, "MISMATCH - invoice 50000 vs weight list 45000 (difference 5000)")
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status=job.status,
                            stage="ERP Submission", note="moved on"))
    job.accepted_verifications = [f"cf:{cf.id}"]
    db_session.commit()

    assert _job_stage(db_session, job) == "ERP Submission"


def test_job_detail_endpoint_includes_the_self_verifying_row(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Amount Verification")
    _set_value(db_session, tenant, job, cf, "MATCH - lines 100.00 = sub total 100.00 = total 100.00")

    op = make_user(db_session, role="operator", tenant=tenant, email="op-selfverify@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["verifications"]
    assert len(rows) == 1
    assert rows[0]["field_label"] == "Amount Verification"
    assert rows[0]["source_document"] == "Custom Field"
    assert rows[0]["target_document"] == ""
    assert rows[0]["status"] == "match"


def test_accepting_a_self_verifying_mismatch_through_the_real_endpoint(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    cf = _make_field(db_session, tenant, group, "Quantity Verification")
    _set_value(db_session, tenant, job, cf, "MISMATCH - invoice 50000 vs weight list 45000 (difference 5000)")

    op = make_user(db_session, role="operator", tenant=tenant, email="op-selfverify2@example.com")
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verification-decision",
                       json={"link_id": f"cf:{cf.id}", "accept": True})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["verifications"]
    assert rows[0]["accepted"] is True

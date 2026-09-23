"""A custom field (an AI-computed value with no position on any document, e.g. "Total Amount
(Calculated)") can now be the SOURCE of a cross-document verification link, checked against a
mark picked directly on another document - "Total" on the Invoice, say. The target stays
mark-only; a custom field is never a target (see CrossDocLink.source_custom_field_id).

_verification_findings shares one key space between mark ids and custom field ids
(_link_source_key), so a custom-field-sourced link is compared exactly like a mark-sourced
one once its own JobFieldValue rows are in play."""
from app.api.v1.jobs import _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", mode="Air Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_invoice_doc(db_session, tenant, group):
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _make_total_mark(db_session, tenant, doc):
    mark = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name="Total",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return mark


def _make_custom_field(db_session, tenant, group, *, label="Total Amount (Calculated)"):
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="ai",
                     ai_prompt="Calculate the total invoice value.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return cf


def test_verification_findings_compares_a_custom_field_against_a_mark_on_another_document(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)

    link = CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                        source_custom_field_id=cf.id, target_mark_id=mark.id)
    db_session.add(link)
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-XVERIFY", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name, extracted_value="1000.00"))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 label_name="Total", extracted_value="1000.00", set_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"
    assert findings[0]["source_value"] == "1000.00"
    assert findings[0]["target_value"] == "1000.00"


def test_verification_findings_reports_a_mismatch_between_custom_field_and_mark(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_custom_field_id=cf.id, target_mark_id=mark.id))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-XMISMATCH", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name, extracted_value="1000.00"))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 label_name="Total", extracted_value="850.00", set_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "mismatch"


def test_link_custom_field_to_marks_creates_the_link_and_sets_the_flag(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)

    admin = make_user(db_session, role="super_admin", email="admin-link@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/custom-fields/{cf.id}/link-marks", json={"target_mark_ids": [mark.id]})
    assert resp.status_code == 201, resp.text
    links = resp.json()
    assert len(links) == 1
    assert links[0]["source_custom_field_id"] == cf.id
    assert links[0]["source_mark_id"] is None
    assert links[0]["target_mark_id"] == mark.id

    db_session.refresh(cf)
    assert cf.verify_with_other_document is True


def test_link_custom_field_to_marks_rejects_a_mark_from_another_group(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    other_group = _make_group(db_session, tenant)
    other_doc = _make_invoice_doc(db_session, tenant, other_group)
    other_mark = _make_total_mark(db_session, tenant, other_doc)
    cf = _make_custom_field(db_session, tenant, group)

    admin = make_user(db_session, role="super_admin", email="admin-link2@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/custom-fields/{cf.id}/link-marks",
                       json={"target_mark_ids": [other_mark.id]})
    assert resp.status_code == 422


def test_unlink_custom_field_from_mark_clears_the_flag_once_nothing_is_linked(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)
    cf.verify_with_other_document = True
    link = CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                        source_custom_field_id=cf.id, target_mark_id=mark.id)
    db_session.add(link)
    db_session.commit()
    db_session.refresh(link)

    admin = make_user(db_session, role="super_admin", email="admin-unlink@example.com")
    login(client, admin.email)

    resp = client.delete(f"/api/v1/custom-fields/{cf.id}/link-marks/{link.id}")
    assert resp.status_code == 204

    db_session.refresh(cf)
    assert cf.verify_with_other_document is False
    # .get() would return the identity-mapped Python object without re-querying; a real
    # query is what actually proves the row is gone.
    assert db_session.query(CrossDocLink).filter(CrossDocLink.id == link.id).first() is None


def test_deleting_the_target_mark_clears_the_custom_fields_flag_too(client, db_session):
    """delete_mark cascades the CrossDocLink away via the FK, but the custom field's own
    verify_with_other_document tick is a separate flag that must be cleared too, or it stays
    stuck true forever with nothing actually linked."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)
    cf.verify_with_other_document = True
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_custom_field_id=cf.id, target_mark_id=mark.id))
    db_session.commit()

    admin = make_user(db_session, role="super_admin", email="admin-delmark@example.com")
    login(client, admin.email)

    resp = client.delete(f"/api/v1/marks/{mark.id}")
    assert resp.status_code == 204

    db_session.refresh(cf)
    assert cf.verify_with_other_document is False
    assert db_session.query(CrossDocLink).filter(
        CrossDocLink.source_custom_field_id == cf.id).count() == 0


def test_job_detail_shows_custom_field_as_the_source_document(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    doc = _make_invoice_doc(db_session, tenant, group)
    mark = _make_total_mark(db_session, tenant, doc)
    cf = _make_custom_field(db_session, tenant, group)
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_custom_field_id=cf.id, target_mark_id=mark.id))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-XDETAIL", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name=cf.label_name, extracted_value="500.00"))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 label_name="Total", extracted_value="500.00", set_index=1))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-xdetail@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["verifications"]
    assert len(rows) == 1
    assert rows[0]["field_label"].startswith("Total Amount (Calculated)")
    assert rows[0]["source_document"] == "Custom Field"
    assert rows[0]["status"] == "match"

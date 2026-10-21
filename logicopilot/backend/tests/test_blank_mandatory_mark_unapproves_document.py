"""PATCH /job-field-values/{value_id} (correct_field_value) - a mandatory mark cleared back to
blank on a document already marked Approved must not leave that badge standing. Found live: an
operator cleared Vessel Name (and several others) on an already-approved Bill of Lading, and it
stayed "✓ Approved" with mandatory fields now missing - nothing had ever touched approval state
on a correction, only a fresh extraction (_begin_extraction) ever reset it.

Un-approving sends the operator back through Submit for Approval, which the frontend's own
check correctly refuses and highlights - this is the backend half that makes that possible."""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import login, make_tenant, make_user


def _make_job(db_session, tenant, *, required: bool):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                     ask_operator_required=required)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-UNAPPROVE", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/invoice.pdf", page_count=1, approved=True, gk2_approved=True)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, job_document_id=jd.id,
                       label_name="Invoice No", extracted_value="INV-001")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    return job, jd, fv


def test_clearing_a_mandatory_mark_unapproves_its_document(client, db_session):
    tenant = make_tenant(db_session)
    job, jd, fv = _make_job(db_session, tenant, required=True)
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-unapprove1@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": ""})
    assert resp.status_code == 200, resp.text

    db_session.refresh(jd)
    assert jd.approved is False
    assert jd.gk2_approved is False


def test_clearing_an_optional_mark_does_not_unapprove_its_document(client, db_session):
    tenant = make_tenant(db_session)
    job, jd, fv = _make_job(db_session, tenant, required=False)
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-unapprove2@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": ""})
    assert resp.status_code == 200, resp.text

    db_session.refresh(jd)
    assert jd.approved is True
    assert jd.gk2_approved is True


def test_clearing_a_mandatory_mark_on_an_unapproved_document_is_a_no_op(client, db_session):
    tenant = make_tenant(db_session)
    job, jd, fv = _make_job(db_session, tenant, required=True)
    jd.approved = False
    jd.gk2_approved = False
    db_session.commit()
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-unapprove3@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": ""})
    assert resp.status_code == 200, resp.text

    db_session.refresh(jd)
    assert jd.approved is False
    assert jd.gk2_approved is False


def test_correcting_a_mandatory_mark_to_a_real_value_leaves_approval_untouched(client, db_session):
    tenant = make_tenant(db_session)
    job, jd, fv = _make_job(db_session, tenant, required=True)
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-unapprove4@example.com")
    login(client, op.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "INV-002"})
    assert resp.status_code == 200, resp.text

    db_session.refresh(jd)
    assert jd.approved is True
    assert jd.gk2_approved is True

"""_hbl_no() — the Jobs list's "BL No" column, inserted between Job No and ETA. Reads the
House Bill of Lading number off a job's own documents - the same "HBL No" label_name marked
on the Bill of Lading, Invoice, and Packing List for cross-checking elsewhere in the app."""
from app.api.v1.jobs import _hbl_no
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-HBL", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_reads_the_hbl_no_mark_off_the_job(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(bl)
    db_session.commit()
    db_session.refresh(bl)
    mark = FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="HBL No",
                      page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                  template_document_id=bl.id, label_name="HBL No",
                                  extracted_value="MAAU1234567"))
    db_session.commit()

    assert _hbl_no(db_session, job) == "MAAU1234567"


def test_no_hbl_no_mark_at_all_is_none(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    assert _hbl_no(db_session, job) is None


def test_blank_hbl_no_on_one_document_falls_back_to_a_real_one_on_another(db_session):
    """HBL No is marked on several documents for cross-checking - a document that happens to
    carry nothing for it must not hide a real reading that exists elsewhere on the same job."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add_all([bl, invoice])
    db_session.commit()
    db_session.refresh(bl)
    db_session.refresh(invoice)
    bl_mark = FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="HBL No",
                         page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    inv_mark = FieldMark(tenant_id=tenant.id, document_id=invoice.id, label_name="HBL No",
                          page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add_all([bl_mark, inv_mark])
    db_session.commit()
    db_session.refresh(bl_mark)
    db_session.refresh(inv_mark)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=bl_mark.id,
                      template_document_id=bl.id, label_name="HBL No", extracted_value=""),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                      template_document_id=invoice.id, label_name="HBL No", extracted_value="MAAU7654321"),
    ])
    db_session.commit()

    assert _hbl_no(db_session, job) == "MAAU7654321"


def test_hbl_no_reaches_the_real_jobs_list_endpoint(client, db_session):
    """End-to-end through GET /jobs, not just the helper in isolation - confirms JobOut/the
    bulk-prefetch wiring in list_jobs actually carries it through to the real response."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-hbl@example.com")

    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(bl)
    db_session.commit()
    db_session.refresh(bl)
    mark = FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="HBL No",
                      page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                  template_document_id=bl.id, label_name="HBL No",
                                  extracted_value="MAAU1234567"))
    db_session.commit()

    login(client, operator.email)
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200, resp.text
    body = {j["id"]: j["hbl_no"] for j in resp.json()}
    assert body[job.id] == "MAAU1234567"


def test_a_different_label_is_never_mistaken_for_hbl_no(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(bl)
    db_session.commit()
    db_session.refresh(bl)
    mark = FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="MBL No",
                      page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                  template_document_id=bl.id, label_name="MBL No",
                                  extracted_value="MASTER123"))
    db_session.commit()

    assert _hbl_no(db_session, job) is None

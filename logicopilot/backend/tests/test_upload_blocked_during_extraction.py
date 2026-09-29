"""upload_job_document and smart_upload both reject a job that is mid-extraction
(status == "extracting") - run_extraction snapshots the job's documents once, at the very
start of its (multi-second to multi-minute) run, and never refreshes that snapshot. A file
landing mid-run is invisible to the loop that writes its fields, even though later steps that
re-query fresh (custom-field computation) do see it - found live on a real job that ended up
with one document's marks entirely blank while everything else on it looked normal. Blocking
the upload itself, rather than trying to make run_extraction tolerate a document list that can
change mid-run, closes the race at its source."""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

def _tiny_pdf_bytes() -> bytes:
    """A genuinely openable 1-page PDF - fitz (PyMuPDF) needs real content to render a page,
    unlike the job-detail-only tests elsewhere in this suite that set JobDocument.file_path
    directly and never touch the real upload endpoint at all."""
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc, **kw):
    kw.setdefault("reference", "JOB-RACE")
    kw.setdefault("status", "draft")
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    # upload_job_document fills an EXISTING slot, the same empty placeholder row real job
    # creation leaves for every template document - it never creates one itself.
    slot = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path=None, page_count=0, file_index=0)
    db_session.add(slot)
    db_session.commit()
    return job


def _login_operator(client, db_session, tenant):
    op = make_user(db_session, role="operator", tenant=tenant, email="op-race@example.com")
    login(client, op.email)
    return op


def test_upload_job_document_rejects_a_job_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc, status="extracting")
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 409


def test_upload_job_document_succeeds_on_a_draft_job(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc, status="draft")
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text


def test_smart_upload_rejects_a_job_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc, status="extracting")
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/smart-upload",
        files={"files": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 409

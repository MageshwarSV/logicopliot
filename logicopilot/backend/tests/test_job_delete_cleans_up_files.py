"""DELETE /jobs/{job_id} (and custom_filter_pages.py's old-job sweep, which shares the same
_delete_job_cascade) removes the job's DB rows, but until now never touched the actual files
on disk - a job's uploaded documents, its IRN supporting documents, and a mail-routed job's
saved original email and attachments all sat there forever after the job "deleting" them.
Confirmed live: this session's own earlier manual cleanup already deleted 4 jobs and 172
documents, whose files are still on disk right now. _remove_document_file (removing ONE file
from a slot) already did this correctly - _delete_job_cascade (removing the WHOLE job) did
not, until this fix.
"""
from app.api.v1.jobs import _job_doc_dir, _supporting_doc_dir
from app.core.job_email import job_email_dir
from app.models.job import Job, JobDocument
from app.models.supporting_document import SupportingDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group_and_job(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DELFILES", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return group, tdoc, job


def test_delete_removes_a_job_documents_own_files(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, job = _make_group_and_job(db_session, tenant)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/invoice.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)

    doc_dir = _job_doc_dir(jd.id)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "original.pdf").write_bytes(b"fake pdf bytes")
    assert doc_dir.exists()

    make_user(db_session, role="super_admin", email="sa-delfiles@example.com")
    login(client, "sa-delfiles@example.com")
    resp = client.delete(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 204, resp.text
    assert not doc_dir.exists()


def test_delete_removes_supporting_document_files(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, job = _make_group_and_job(db_session, tenant)
    sd = SupportingDocument(tenant_id=tenant.id, job_id=job.id, label="COO",
                            files=[{"stored_as": "0.pdf", "original_name": "coo.pdf", "size": 3}])
    db_session.add(sd)
    db_session.commit()
    db_session.refresh(sd)

    sd_dir = _supporting_doc_dir(sd.id)
    sd_dir.mkdir(parents=True, exist_ok=True)
    (sd_dir / "0.pdf").write_bytes(b"AAA")
    assert sd_dir.exists()

    make_user(db_session, role="super_admin", email="sa-delfiles2@example.com")
    login(client, "sa-delfiles2@example.com")
    resp = client.delete(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 204, resp.text
    assert not sd_dir.exists()


def test_delete_removes_the_saved_original_email(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, job = _make_group_and_job(db_session, tenant)

    email_dir = job_email_dir(job.id)
    (email_dir / "attachments").mkdir(parents=True, exist_ok=True)
    (email_dir / "original.eml").write_bytes(b"From: a@b.com\r\n\r\nBody")
    assert email_dir.exists()

    make_user(db_session, role="super_admin", email="sa-delfiles3@example.com")
    login(client, "sa-delfiles3@example.com")
    resp = client.delete(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 204, resp.text
    assert not email_dir.exists()

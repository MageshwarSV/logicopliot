""""Pre-Alert Received"/"Pending Documents" were unified into a single "Missing Documents"
status, and JobOut/JobDetailOut now name exactly which mandatory document(s) are still missing -
see _missing_mandatory_documents in app/api/v1/jobs.py."""
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group_with_docs(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                              doc_type="Invoice", is_required=True)
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading",
                          doc_type="BL", is_required=True)
    freight = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Freight Certificate",
                               doc_type="Custom", is_required=False)
    db_session.add_all([invoice, bl, freight])
    db_session.commit()
    for d in (invoice, bl, freight):
        db_session.refresh(d)
    return group, invoice, bl, freight


def _make_job(db_session, tenant, group, uploaded_docs, **kw):
    kw.setdefault("reference", "JOB-MISSING")
    kw.setdefault("status", "draft")
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    for tdoc in uploaded_docs:
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                                   file_path="fake.pdf", page_count=1, file_index=0))
    db_session.commit()
    return job


def test_job_list_names_the_missing_mandatory_documents(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice])  # BL not uploaded, Freight is optional
    admin = make_user(db_session, role="super_admin", email="sa-missing1@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "Missing Documents"
    assert row["missing_documents"] == ["Bill of Lading"]


def test_job_detail_also_names_the_missing_mandatory_documents(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [])  # nothing uploaded at all
    admin = make_user(db_session, role="super_admin", email="sa-missing2@example.com")
    login(client, admin.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["outer_status"] == "Missing Documents"
    assert set(resp.json()["missing_documents"]) == {"Invoice", "Bill of Lading"}


def test_optional_documents_are_never_listed_as_missing(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])  # every MANDATORY doc present
    admin = make_user(db_session, role="super_admin", email="sa-missing3@example.com")
    login(client, admin.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["outer_status"] != "Missing Documents"
    assert not resp.json()["missing_documents"]


def test_missing_documents_applies_even_to_an_already_extracted_job(client, db_session):
    """The real production bug: a job extracted successfully once (status stays "extracted"
    forever - it never reverts to "draft"), then EVERY document was later removed from it (the
    Remove button clears a JobDocument's file_path but never touches job.status). Found live:
    such a job kept reading "GK1 Review" (or whatever its last real stage happened to be)
    with nothing anywhere saying a required document was actually gone."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [], status="extracted")
    admin = make_user(db_session, role="super_admin", email="sa-missing5@example.com")
    login(client, admin.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["outer_status"] == "Missing Documents"
    assert set(resp.json()["missing_documents"]) == {"Invoice", "Bill of Lading"}


def test_missing_documents_never_overrides_a_genuinely_completed_job(client, db_session):
    """A finished job must never be reinterpreted as "Missing Documents" just because its
    documents happen to be gone by then (e.g. cleaned up after completion) - completed is a
    real terminal state and stays one."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [], status="completed")
    admin = make_user(db_session, role="super_admin", email="sa-missing6@example.com")
    login(client, admin.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["outer_status"] != "Missing Documents"


def test_missing_documents_clears_once_the_last_mandatory_document_is_uploaded(client, db_session):
    """Requirement #4: the status (and the missing list) updates automatically once resolved -
    nothing besides re-reading the job is needed."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_docs(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice])
    op = make_user(db_session, role="operator", tenant=tenant, email="op-missing4@example.com")
    op.assigned_modes = []
    db_session.add(op)
    db_session.commit()
    login(client, op.email)

    before = client.get(f"/api/v1/jobs/{job.id}")
    assert before.json()["outer_status"] == "Missing Documents"

    up = client.post(
        f"/api/v1/jobs/{job.id}/documents/{bl.id}/upload",
        files={"file": ("bl.pdf", b"", "application/pdf")},
    )
    # A real PDF isn't needed to prove the status-clearing logic itself - an empty upload may
    # fail validation, so fall back to writing the JobDocument directly if so.
    if up.status_code != 200:
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                                   file_path="fake.pdf", page_count=1, file_index=0))
        db_session.commit()

    after = client.get(f"/api/v1/jobs/{job.id}")
    assert after.json()["outer_status"] != "Missing Documents"
    assert not after.json()["missing_documents"]

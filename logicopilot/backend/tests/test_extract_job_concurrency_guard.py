"""Manual Extract / Re-run extraction must refuse outright while a run is already in flight
for this job - without this, a click landing while the auto-trigger's own background
run_extraction was still running started a SECOND, fully concurrent run. Both independently
do their own idempotent check-then-write per field with no lock between them, so a field's
write from each run can land before the other's check sees it - found live: a Bill of
Lading's Gross Wt and package_count each ended up with two JobFieldValue rows for the same
single-value mark. Mirrors the identical, already-existing guard in upload_job_document and
smart_upload (same file) - this closes the one place that pair of guards didn't cover."""
from unittest.mock import patch

from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def test_refuses_with_409_while_the_job_is_already_extracting(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CONCURRENT-TEST",
             status="extracting")
    db_session.add(job)
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="concurrent-op@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 409
    assert "already being read" in resp.json()["detail"]
    mock_start.assert_not_called()


def test_a_draft_job_still_starts_extraction_normally(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CONCURRENT-TEST-2",
             status="draft")
    db_session.add(job)
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="concurrent-op2@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "extracting"
    mock_start.assert_called_once()


# ---------------------------------------------------------------- required-document guard


def _make_group_with_docs(db_session, tenant, *, pl_required=True):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading",
                          doc_type="BL", is_required=True)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", is_required=True)
    pl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                          doc_type="PackingList", is_required=pl_required)
    db_session.add_all([bl, inv, pl])
    db_session.commit()
    for td in (bl, inv, pl):
        db_session.refresh(td)
    return group, bl, inv, pl


def test_refuses_a_drafts_first_pass_when_a_required_document_is_missing(client, db_session):
    """The exact production bug: BL and Invoice uploaded, Packing List (required) never
    uploaded - the manual Extract button let this job jump straight to "extracted" with no
    check at all, so it never showed "Missing Documents" and the missing document's fields
    just sat empty forever."""
    tenant = make_tenant(db_session)
    group, bl, inv, pl = _make_group_with_docs(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MISSING-PL", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                   file_path="fake/bl.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                   file_path="fake/invoice.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl.id),  # no file
    ])
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="missing-pl-op@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 409, resp.text
    assert "Packing List" in resp.json()["detail"]
    mock_start.assert_not_called()
    db_session.refresh(job)
    assert job.status == "draft"  # unchanged - never started


def test_proceeds_once_every_required_document_is_present(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, inv, pl = _make_group_with_docs(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ALL-PRESENT", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                   file_path="fake/bl.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                   file_path="fake/invoice.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl.id,
                   file_path="fake/pl.pdf", page_count=1),
    ])
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="all-present-op@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 200, resp.text
    mock_start.assert_called_once()


def test_an_optional_document_is_never_waited_on(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, inv, pl = _make_group_with_docs(db_session, tenant, pl_required=False)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PL-OPTIONAL", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                   file_path="fake/bl.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                   file_path="fake/invoice.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl.id),  # no file
    ])
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="pl-optional-op@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 200, resp.text
    mock_start.assert_called_once()


def test_a_re_run_on_an_already_extracted_job_is_never_blocked_by_this_check(client, db_session):
    """Deliberately unchanged: an operator re-running a job that is already past its first
    pass, with one required document still absent on purpose (permanently unavailable, say),
    must still be able to."""
    tenant = make_tenant(db_session)
    group, bl, inv, pl = _make_group_with_docs(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-RERUN-MISSING-PL",
             status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                   file_path="fake/bl.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                   file_path="fake/invoice.pdf", page_count=1),
        JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl.id),  # no file
    ])
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="rerun-op@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        resp = client.post(f"/api/v1/jobs/{job.id}/extract")

    assert resp.status_code == 200, resp.text
    mock_start.assert_called_once()

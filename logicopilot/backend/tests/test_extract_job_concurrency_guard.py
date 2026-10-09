"""Manual Extract / Re-run extraction must refuse outright while a run is already in flight
for this job - without this, a click landing while the auto-trigger's own background
run_extraction was still running started a SECOND, fully concurrent run. Both independently
do their own idempotent check-then-write per field with no lock between them, so a field's
write from each run can land before the other's check sees it - found live: a Bill of
Lading's Gross Wt and package_count each ended up with two JobFieldValue rows for the same
single-value mark. Mirrors the identical, already-existing guard in upload_job_document and
smart_upload (same file) - this closes the one place that pair of guards didn't cover."""
from unittest.mock import patch

from app.models.job import Job
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

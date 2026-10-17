"""Tenant.gk2_required_by_mode - a Tenant Admin can turn GK2 review off for a shipment type,
so GK1's own "Final Submit for Approval" goes straight into the real ERP submission instead of
a human GK2's queue. Missing key/null column = GK2 required (unchanged default behavior)."""
import time as _time
from unittest.mock import patch

from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, *, status="extracted", validation_approved=True,
              irn_approval_requested=False):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference="JOB-SKIPGK2",
        status=status, validation_approved=validation_approved,
        irn_approval_requested=irn_approval_requested,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _wait_for(db_session, job, attr, value, tries=20):
    for _ in range(tries):
        db_session.expire_all()
        fresh = db_session.get(Job, job.id)
        if getattr(fresh, attr) == value:
            return fresh
        _time.sleep(0.05)
    raise AssertionError(f"{attr} never reached {value!r}")


# --------------------------------------------------------------- PATCH /tenants validation


def test_tenant_admin_sets_gk2_required_by_mode(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-gk2mode1@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"gk2_required_by_mode": {"Sea Import": False}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_required_by_mode"] == {"Sea Import": False}


def test_gk2_required_by_mode_rejects_an_invalid_mode(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-gk2mode2@example.com")
    login(client, ta.email)

    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"gk2_required_by_mode": {"Not A Mode": False}})
    assert resp.status_code == 400


def test_gk2_required_by_mode_merges_not_replaces(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-gk2mode3@example.com")
    login(client, ta.email)

    client.patch(f"/api/v1/tenants/{tenant.id}", json={"gk2_required_by_mode": {"Sea Import": False}})
    resp = client.patch(f"/api/v1/tenants/{tenant.id}", json={"gk2_required_by_mode": {"Air Import": False}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_required_by_mode"] == {"Sea Import": False, "Air Import": False}


# --------------------------------------------------------------- submit-for-approval skip


def test_submit_for_gk2_approval_still_requires_gk2_by_default(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip1@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gk2_status"] == "pending"
    assert body["gk2_skipped"] is False


def test_submit_for_gk2_approval_skips_gk2_when_turned_off_for_the_mode(client, db_session, session_factory):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip2@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    with patch("time.sleep", return_value=None), \
         patch("app.db.session.SessionLocal", session_factory):
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["gk2_skipped"] is True
        assert body["gk2_status"] == "preparing_erp"
        # Never passes through a human GK2's queue.
        assert body["gk2_validation_approved"] is False
        _wait_for(db_session, job, "gk2_status", "submitted")


def test_submit_for_gk2_approval_still_requires_gk2_for_an_unaffected_mode(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Air Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip3@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gk2_status"] == "pending"
    assert body["gk2_skipped"] is False


def test_skip_gk2_respects_irn_approval_requested(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip4@example.com")
    job = _make_job(db_session, tenant, group, irn_approval_requested=True)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gk2_skipped"] is True
    assert body["gk2_status"] == "irn_document_process"


def test_skip_gk2_still_blocks_on_unanswered_required_fields(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip5@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    with patch("app.api.v1.jobs._unanswered_operator_fields", return_value=["Some Field"]):
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 409
    db_session.refresh(job)
    assert job.gk2_status is None  # never advanced


def test_skipped_job_never_appears_in_a_gk2_pending_queue(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip6@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)
    with patch("time.sleep", return_value=None):
        client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-skip6@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    resp = client.get("/api/v1/jobs", params={"gk2_status": "pending"})
    assert job.id not in {j["id"] for j in resp.json()}


# --------------------------------------------------------------- predictive gk2_required
# (known BEFORE Final Submit is pressed - lets GK1's own screen say up front whether a job is
# headed to GK2 or straight to the real ERP, unlike gk2_skipped which is only set afterward)


def test_job_detail_reports_gk2_required_true_by_default(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-predict1@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_required"] is True


def test_job_detail_reports_gk2_required_false_when_turned_off_for_the_mode(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-predict2@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    # Known ahead of time - before this job has been submitted at all.
    assert job.gk2_status is None
    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_required"] is False


def test_job_detail_reports_gk2_required_true_for_an_unaffected_mode(client, db_session):
    tenant = make_tenant(db_session)
    tenant.gk2_required_by_mode = {"Sea Import": False}
    db_session.commit()
    group = _make_group(db_session, tenant, mode="Air Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-predict3@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_required"] is True


def test_no_config_means_gk2_required_mirrors_old_behavior(client, db_session):
    """A tenant with nothing saved for gk2_required_by_mode (every tenant before this
    feature existed) behaves exactly as before - nothing to configure, nothing changes."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-skip7@example.com")
    job = _make_job(db_session, tenant, group)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_status"] == "pending"

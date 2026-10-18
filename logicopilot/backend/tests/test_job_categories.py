"""The Jobs list's three left-side screens - New Jobs / Submitted Jobs / Completed Jobs - and
the "Verified" action that moves a job from Submitted to Completed. Applies identically
whether a tenant/mode requires GK2 review or has it turned off (see Tenant.gk2_required_by_mode
/ test_gk2_optional_by_mode.py): both paths reach the same gk2_status == "submitted" outcome
through _finalize_job_approval."""

from datetime import datetime, timezone

from app.api.v1.jobs import _job_category
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kwargs):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference=kwargs.pop("reference", "JOB-CAT"),
        status=kwargs.pop("status", "extracted"), **kwargs,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


# --------------------------------------------------------------- _job_category (pure logic)


def test_an_ordinary_job_is_new():
    job = Job(status="extracted", gk2_status=None, erp_verified_at=None)
    assert _job_category(job) == "new"


def test_pending_gk2_approval_is_still_new():
    job = Job(status="extracted", gk2_status="pending", erp_verified_at=None)
    assert _job_category(job) == "new"


def test_preparing_or_entering_erp_is_still_new():
    assert _job_category(Job(status="extracted", gk2_status="preparing_erp")) == "new"
    assert _job_category(Job(status="extracted", gk2_status="entering_erp")) == "new"


def test_a_failed_erp_run_is_still_new_not_lost():
    # Retryable by GK2 pressing Final Approve & Proceed again - must stay visible in New
    # Jobs, not vanish into neither bucket.
    job = Job(status="extracted", gk2_status="failed")
    assert _job_category(job) == "new"


def test_ai_erp_submitted_and_unverified_is_submitted():
    job = Job(status="extracted", gk2_status="submitted", erp_verified_at=None)
    assert _job_category(job) == "submitted"


def test_ai_erp_submitted_and_verified_is_completed():
    job = Job(status="extracted", gk2_status="submitted",
             erp_verified_at=datetime.now(timezone.utc))
    assert _job_category(job) == "completed"


def test_direct_submit_entry_path_is_completed_with_no_verification_needed():
    # complete_job's own path - job.status goes straight to "completed", gk2_status never
    # set at all. An operator pressing Submit Entry already IS the human confirming it.
    job = Job(status="completed", gk2_status=None, erp_verified_at=None)
    assert _job_category(job) == "completed"


# --------------------------------------------------------------- GET /jobs?category=...


def test_category_new_excludes_submitted_and_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op1@example.com")
    new_job = _make_job(db_session, tenant, group, reference="JOB-NEW")
    submitted_job = _make_job(db_session, tenant, group, reference="JOB-SUB", gk2_status="submitted")
    completed_job = _make_job(db_session, tenant, group, reference="JOB-DONE", status="completed")
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "new"})
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert new_job.id in ids
    assert submitted_job.id not in ids
    assert completed_job.id not in ids


def test_category_submitted_is_only_unverified_ai_erp_submissions(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op2@example.com")
    unverified = _make_job(db_session, tenant, group, reference="JOB-UNVER", gk2_status="submitted")
    verified = _make_job(db_session, tenant, group, reference="JOB-VER", gk2_status="submitted",
                         erp_verified_at=datetime.now(timezone.utc))
    pending = _make_job(db_session, tenant, group, reference="JOB-PEND", gk2_status="pending")
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "submitted"})
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert ids == {unverified.id}
    assert verified.id not in ids
    assert pending.id not in ids


def test_category_completed_includes_both_verified_and_direct_submit_entry(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op3@example.com")
    verified = _make_job(db_session, tenant, group, reference="JOB-VER2", gk2_status="submitted",
                         erp_verified_at=datetime.now(timezone.utc))
    direct = _make_job(db_session, tenant, group, reference="JOB-DIRECT", status="completed")
    new_job = _make_job(db_session, tenant, group, reference="JOB-NEW2")
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "completed"})
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert ids == {verified.id, direct.id}
    assert new_job.id not in ids


def test_category_param_omitted_returns_every_category_unchanged(client, db_session):
    # Every existing caller (dashboard, IRN Pending) never sends category - must keep seeing
    # everything, exactly as before this feature existed.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op4@example.com")
    a = _make_job(db_session, tenant, group, reference="JOB-A")
    b = _make_job(db_session, tenant, group, reference="JOB-B", gk2_status="submitted")
    c = _make_job(db_session, tenant, group, reference="JOB-C", status="completed")
    login(client, op.email)

    resp = client.get("/api/v1/jobs")
    ids = {j["id"] for j in resp.json()}
    assert {a.id, b.id, c.id} <= ids


def test_invalid_category_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op5@example.com")
    login(client, op.email)
    resp = client.get("/api/v1/jobs", params={"category": "not-a-real-one"})
    assert resp.status_code == 400


def test_gk1_only_skipped_job_reaches_submitted_category_the_same_as_a_gk2_reviewed_one(client, db_session):
    # The user's explicit requirement: this three-way split applies "in GK1 or GK2 both" -
    # a tenant with GK2 turned off for this mode still reaches gk2_status == "submitted" via
    # _finalize_job_approval's skip path, and must land on the same Submitted Jobs screen.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op6@example.com")
    skipped_and_submitted = _make_job(
        db_session, tenant, group, reference="JOB-SKIPPED", gk2_status="submitted", gk2_skipped=True,
    )
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "submitted"})
    ids = {j["id"] for j in resp.json()}
    assert skipped_and_submitted.id in ids


# --------------------------------------------------------------- GET /jobs/category-counts


def test_category_counts_matches_the_filtered_lists(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op7@example.com")
    _make_job(db_session, tenant, group, reference="JOB-N1")
    _make_job(db_session, tenant, group, reference="JOB-N2")
    _make_job(db_session, tenant, group, reference="JOB-S1", gk2_status="submitted")
    _make_job(db_session, tenant, group, reference="JOB-C1", status="completed")
    login(client, op.email)

    resp = client.get("/api/v1/jobs/category-counts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"new": 2, "submitted": 1, "completed": 1}


# --------------------------------------------------------------- POST /jobs/{id}/verify


def test_verify_moves_a_submitted_job_to_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="cat-gk2a@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    job = _make_job(db_session, tenant, group, gk2_status="submitted")
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["category"] == "completed"
    assert body["erp_verified_at"] is not None
    assert body["erp_verified_by_name"] == gk2.full_name

    db_session.refresh(job)
    assert job.erp_verified_at is not None
    assert job.erp_verified_by_id == gk2.id


def test_verify_refuses_a_job_that_was_never_submitted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="cat-admin1@example.com")
    job = _make_job(db_session, tenant, group, gk2_status="pending")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 409


def test_verify_is_idempotent_not_an_error_on_a_second_press(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="cat-admin2@example.com")
    job = _make_job(db_session, tenant, group, gk2_status="submitted")
    login(client, admin.email)

    first = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert first.status_code == 200
    first_ts = first.json()["erp_verified_at"]

    second = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert second.status_code == 200
    assert second.json()["erp_verified_at"] == first_ts  # unchanged, not re-stamped


def test_verify_is_refused_to_a_plain_operator(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op8@example.com")
    job = _make_job(db_session, tenant, group, gk2_status="submitted")
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 403

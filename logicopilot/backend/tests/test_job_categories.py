"""The Jobs list's three left-side screens - New Jobs / Submitted Jobs / Completed Jobs - and
the "Verified" action that moves a job from Submitted to Completed. Applies identically
whether a tenant/mode requires GK2 review or has it turned off (see Tenant.gk2_required_by_mode
/ test_gk2_optional_by_mode.py): both paths reach the same successful outcome through
_finalize_job_approval, and Job.ai_submitted_at is set identically either way.

Job.ai_submitted_at, not gk2_status, is the real signal for "this job went through the AI/ERP
automation" - a job found live in production proved why: persist_run_outcome deliberately
resets gk2_status to None on a LATER retry through the regular entry screen (an Entry Browser
rerun, complete_job), so a stale "Failed"/"AI - ERP Submitted" badge does not linger after a
fresh attempt. That reset is correct for the badge, but it silently un-submitted jobs on the
Jobs list - gk2_status=="submitted" jobs that had since been retried fell straight into
Completed Jobs with nobody ever having pressed Verified. ai_submitted_at is set once, at the
moment the AI path first succeeds, and never cleared again, so it survives that reset."""

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


NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------- _job_category (pure logic)


def test_an_ordinary_job_is_new():
    job = Job(status="extracted", gk2_status=None, ai_submitted_at=None, erp_verified_at=None)
    assert _job_category(job) == "new"


def test_pending_gk2_approval_is_still_new():
    job = Job(status="extracted", gk2_status="pending", ai_submitted_at=None)
    assert _job_category(job) == "new"


def test_preparing_or_entering_erp_is_still_new():
    assert _job_category(Job(status="extracted", gk2_status="preparing_erp", ai_submitted_at=None)) == "new"
    assert _job_category(Job(status="extracted", gk2_status="entering_erp", ai_submitted_at=None)) == "new"


def test_a_failed_erp_run_is_still_new_not_lost():
    # Retryable by GK2 pressing Final Approve & Proceed again - must stay visible in New
    # Jobs, not vanish into neither bucket. ai_submitted_at is never set on a run that has
    # only ever failed so far.
    job = Job(status="extracted", gk2_status="failed", ai_submitted_at=None)
    assert _job_category(job) == "new"


def test_ai_erp_submitted_and_unverified_is_submitted():
    # The realistic shape: persist_run_outcome sets job.status = "completed" on this SAME
    # successful run, ai_submitted_at is set by _kick_off_real_erp_submission's success
    # branch, and gk2_status also happens to read "submitted" right now - all three true at
    # once immediately after a fresh, un-retried success.
    job = Job(status="completed", gk2_status="submitted", ai_submitted_at=NOW, erp_verified_at=None)
    assert _job_category(job) == "submitted"


def test_ai_erp_submitted_and_verified_is_completed():
    job = Job(status="completed", gk2_status="submitted", ai_submitted_at=NOW,
             erp_verified_at=datetime.now(timezone.utc))
    assert _job_category(job) == "completed"


def test_direct_submit_entry_path_is_completed_with_no_verification_needed():
    # complete_job's own path - job.status goes straight to "completed", ai_submitted_at
    # never set at all. An operator pressing Submit Entry already IS the human confirming it.
    job = Job(status="completed", gk2_status=None, ai_submitted_at=None, erp_verified_at=None)
    assert _job_category(job) == "completed"


def test_the_old_placeholder_non_excel_path_is_also_submitted_not_completed():
    # The non-Excel-entry placeholder outcome never calls persist_run_outcome at all, so
    # job.status stays whatever it was before (typically "extracted") - ai_submitted_at alone
    # must still be enough to land this on Submitted Jobs.
    job = Job(status="extracted", gk2_status="submitted", ai_submitted_at=NOW, erp_verified_at=None)
    assert _job_category(job) == "submitted"


def test_a_later_retry_through_the_regular_entry_screen_does_not_un_submit_the_job():
    # THE bug found live in production. persist_run_outcome resets gk2_status to None on a
    # later retry through the regular entry screen (an Entry Browser rerun, complete_job) -
    # on purpose, so a stale badge doesn't linger - and that retry can itself fail afterward
    # too. A job that genuinely went through the AI path once must stay on Submitted Jobs
    # (awaiting Verified) through all of that churn, never sliding back to New or jumping
    # straight to Completed just because gk2_status/job.status moved on without it.
    job = Job(status="failed", gk2_status=None, ai_submitted_at=NOW, erp_verified_at=None)
    assert _job_category(job) == "submitted"

    job.status = "completed"  # the retry then succeeds on its own, via the regular screen
    assert _job_category(job) == "submitted"  # still not Completed - nobody pressed Verified

    job.erp_verified_at = datetime.now(timezone.utc)
    assert _job_category(job) == "completed"


# --------------------------------------------------------------- GET /jobs?category=...


def test_category_new_excludes_submitted_and_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op1@example.com")
    new_job = _make_job(db_session, tenant, group, reference="JOB-NEW")
    submitted_job = _make_job(db_session, tenant, group, reference="JOB-SUB",
                              status="completed", gk2_status="submitted", ai_submitted_at=NOW)
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
    unverified = _make_job(db_session, tenant, group, reference="JOB-UNVER",
                           status="completed", gk2_status="submitted", ai_submitted_at=NOW)
    verified = _make_job(db_session, tenant, group, reference="JOB-VER",
                         status="completed", gk2_status="submitted", ai_submitted_at=NOW,
                         erp_verified_at=datetime.now(timezone.utc))
    pending = _make_job(db_session, tenant, group, reference="JOB-PEND", gk2_status="pending")
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "submitted"})
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert ids == {unverified.id}
    assert verified.id not in ids
    assert pending.id not in ids


def test_category_submitted_survives_a_gk2_status_reset_from_a_later_retry(client, db_session):
    # The exact production bug, at the HTTP layer: ai_submitted_at set once, but gk2_status
    # has since been reset to None (and status bounced to "failed" and back) by a later
    # retry through the regular entry screen - must still show on Submitted Jobs.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op2b@example.com")
    drifted = _make_job(db_session, tenant, group, reference="JOB-DRIFTED",
                        status="completed", gk2_status=None, gk2_skipped=True, ai_submitted_at=NOW)
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "submitted"})
    assert resp.status_code == 200, resp.text
    assert drifted.id in {j["id"] for j in resp.json()}

    resp = client.get("/api/v1/jobs", params={"category": "completed"})
    assert drifted.id not in {j["id"] for j in resp.json()}


def test_category_completed_includes_both_verified_and_direct_submit_entry(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op3@example.com")
    verified = _make_job(db_session, tenant, group, reference="JOB-VER2",
                         status="completed", gk2_status="submitted", ai_submitted_at=NOW,
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
    b = _make_job(db_session, tenant, group, reference="JOB-B",
                 status="completed", gk2_status="submitted", ai_submitted_at=NOW)
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
    # a tenant with GK2 turned off for this mode still sets ai_submitted_at via the exact same
    # _finalize_job_approval -> _kick_off_real_erp_submission path, and must land on the same
    # Submitted Jobs screen.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op6@example.com")
    skipped_and_submitted = _make_job(
        db_session, tenant, group, reference="JOB-SKIPPED",
        status="completed", gk2_status="submitted", gk2_skipped=True, ai_submitted_at=NOW,
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
    _make_job(db_session, tenant, group, reference="JOB-S1",
             status="completed", gk2_status="submitted", ai_submitted_at=NOW)
    _make_job(db_session, tenant, group, reference="JOB-C1", status="completed")
    login(client, op.email)

    resp = client.get("/api/v1/jobs/category-counts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"new": 2, "missing": 0, "failed": 0, "submitted": 1, "completed": 1}


def _make_group_with_required_doc(db_session, tenant):
    from app.models.template_document import TemplateDocument

    group = _make_group(db_session, tenant)
    db_session.add(TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                                    doc_type="Invoice", is_required=True))
    db_session.commit()
    return group


def test_category_counts_includes_missing_and_failed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group_with_required_doc(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op8@example.com")
    _make_job(db_session, tenant, group, reference="JOB-DRAFT1", status="draft")
    _make_job(db_session, tenant, group, reference="JOB-DRAFT2", status="draft")
    _make_job(db_session, tenant, group, reference="JOB-FAILED-STATUS", status="failed")
    _make_job(db_session, tenant, group, reference="JOB-FAILED-GK2",
             status="extracted", gk2_status="failed")
    login(client, op.email)

    resp = client.get("/api/v1/jobs/category-counts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["missing"] == 2
    assert body["failed"] == 2


def test_missing_category_filters_the_jobs_list_to_draft_jobs(client, db_session):
    from app.models.job import JobDocument

    tenant = make_tenant(db_session)
    group = _make_group_with_required_doc(db_session, tenant)
    invoice_tdoc_id = group.documents[0].id
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op9@example.com")
    draft = _make_job(db_session, tenant, group, reference="JOB-DRAFT3", status="draft")
    has_its_doc = _make_job(db_session, tenant, group, reference="JOB-EXTRACTED1")
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=has_its_doc.id,
                               template_document_id=invoice_tdoc_id, file_path="fake.pdf", page_count=1))
    db_session.commit()
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "missing"})
    ids = {j["id"] for j in resp.json()}
    assert ids == {draft.id}


def test_missing_category_also_finds_an_already_extracted_job_stripped_of_documents(client, db_session):
    """The real production bug: a job extracted once (status stays "extracted" forever once it
    is), then had every document later removed - still genuinely missing a mandatory document,
    so it must still show up on the "missing" screen, not just a job still in "draft"."""
    from app.models.job import JobDocument

    tenant = make_tenant(db_session)
    group = _make_group_with_required_doc(db_session, tenant)
    invoice_tdoc_id = group.documents[0].id
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op11@example.com")
    stripped = _make_job(db_session, tenant, group, reference="JOB-STRIPPED", status="extracted")
    has_its_doc = _make_job(db_session, tenant, group, reference="JOB-NOT-MISSING", status="extracted")
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=has_its_doc.id,
                               template_document_id=invoice_tdoc_id, file_path="fake.pdf", page_count=1))
    db_session.commit()
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "missing"})
    ids = {j["id"] for j in resp.json()}
    assert ids == {stripped.id}

    counts = client.get("/api/v1/jobs/category-counts").json()
    assert counts["missing"] == 1


def test_failed_category_filters_the_jobs_list_to_failed_jobs_either_way(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op10@example.com")
    failed_status = _make_job(db_session, tenant, group, reference="JOB-F1", status="failed")
    failed_gk2 = _make_job(db_session, tenant, group, reference="JOB-F2",
                           status="extracted", gk2_status="failed")
    _make_job(db_session, tenant, group, reference="JOB-OK")
    login(client, op.email)

    resp = client.get("/api/v1/jobs", params={"category": "failed"})
    ids = {j["id"] for j in resp.json()}
    assert ids == {failed_status.id, failed_gk2.id}


# --------------------------------------------------------------- POST /jobs/{id}/verify


def test_verify_moves_a_submitted_job_to_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="cat-gk2a@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    job = _make_job(db_session, tenant, group, status="completed", gk2_status="submitted", ai_submitted_at=NOW)
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


def test_verify_still_works_after_a_gk2_status_reset_from_a_later_retry(client, db_session):
    # The exact production bug, on the verify endpoint itself: a job whose gk2_status has
    # since been reset to None must still be verifiable, since the Jobs list already shows
    # it as Submitted based on ai_submitted_at.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="cat-admin3@example.com")
    job = _make_job(db_session, tenant, group, status="completed", gk2_status=None,
                    gk2_skipped=True, ai_submitted_at=NOW)
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 200, resp.text
    assert resp.json()["category"] == "completed"


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
    job = _make_job(db_session, tenant, group, status="completed", gk2_status="submitted", ai_submitted_at=NOW)
    login(client, admin.email)

    first = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert first.status_code == 200
    first_ts = first.json()["erp_verified_at"]

    second = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert second.status_code == 200
    assert second.json()["erp_verified_at"] == first_ts  # unchanged, not re-stamped


def test_verify_is_allowed_for_an_operator(client, db_session):
    # Gate Keeper 1 can press Verified too - for a GK2-skipped tenant they are often the only
    # one who ever sees this job at all.
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-op8@example.com")
    job = _make_job(db_session, tenant, group, status="completed", gk2_status="submitted", ai_submitted_at=NOW)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 200, resp.text


def test_verify_is_refused_to_a_read_only_manager(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    manager = make_user(db_session, role="manager", tenant=tenant, email="cat-mgr1@example.com")
    job = _make_job(db_session, tenant, group, status="completed", gk2_status="submitted", ai_submitted_at=NOW)
    login(client, manager.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/verify")
    assert resp.status_code == 403


# ---------------------------------------------------------------- mark-erp-submitted


def test_mark_erp_submitted_overrides_a_falsely_reported_failure(client, db_session):
    """The exact production bug: a run submitted cleanly (erp_captured already held a real
    ERP_Job_No matching this job's own reference) and only crashed LATER on an unrelated extra
    screen, so persist_run_outcome marked the whole run failed despite the entry having gone
    through."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="cat-mark1@example.com")
    job = _make_job(
        db_session, tenant, group, reference="JOB-ABCDEF", status="failed", gk2_status=None,
        gk2_skipped=True, erp_status="failed", erp_reason="The web-entry got stuck too long.",
        erp_captured={"ERP_Job_No": {"label": "ERP_Job_No", "value": "MAA/SI/03427/26-27",
                                     "kind": "value", "description": "", "file": None,
                                     "usage": "output"}},
    )
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/mark-erp-submitted")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["erp_status"] == "ok"
    assert body["gk2_status"] == "submitted"
    assert body["reference"] == "MAA/SI/03427/26-27"  # placeholder promoted to the real one

    db_session.refresh(job)
    assert job.ai_submitted_at is not None
    assert job.erp_captured["ERP_Job_No"]["value"] == "MAA/SI/03427/26-27"  # untouched


def test_mark_erp_submitted_refused_without_a_captured_erp_job_number(client, db_session):
    """No blanket override - refuses when there is no actual evidence the run ever reached
    the ERP at all, not just a human's say-so."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="cat-mark2@example.com")
    job = _make_job(db_session, tenant, group, status="failed", erp_captured=None)
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/mark-erp-submitted")
    assert resp.status_code == 409

    db_session.refresh(job)
    assert job.status == "failed"  # untouched


def test_mark_erp_submitted_is_refused_to_an_operator(client, db_session):
    """Super Admin only - this is overriding what the automation itself reported, not an
    ordinary operator action."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="cat-mark3@example.com")
    job = _make_job(
        db_session, tenant, group, status="failed",
        erp_captured={"ERP_Job_No": {"value": "MAA/SI/03427/26-27"}},
    )
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/mark-erp-submitted")
    assert resp.status_code == 403

"""GK2's "Final Approve & Proceed" moves the job through three stages, tested here by calling
the background logic (_run_gk2_submission) directly, the same way the endpoint's own
background thread calls it, rather than through a real thread against the production
database, which a test's isolated session could never observe:

  preparing_erp ("AI - Preparing for ERP")  - the import workbook is built
  entering_erp  ("ERP Entry Process Started") - the tenant's ready ErpScript is replayed
                                                 against the real ERP for real (play_steps is
                                                 mocked here - a real browser never launches in
                                                 a test), with the workbook attached to its
                                                 recorded upload step
  submitted     ("AI - ERP Submitted")  OR  failed ("Failed")  - whatever that run reports,
                                             through persist_run_outcome - the same function
                                             complete_job's own Submit Entry goes through.

A build failure, an empty workbook, no ready script, a script with nowhere to attach the
workbook, or the run itself failing all land on gk2_status "failed" ("Failed") with the reason
on erp_status/erp_reason - not a dead end: gk2_approve accepts "pending" OR "failed", so GK2
can press Final Approve & Proceed again themselves once whatever's wrong is fixed. "Download
as Excel" (download_job_excel) shares the same workbook build, but never runs anything - it is
unaffected by any of this."""
from unittest.mock import patch

from app.api.v1.jobs import _run_gk2_submission
from app.models.erp_script import ErpScript
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

EXCEL_CONFIG = {
    "source": "blank",
    "file_name": "import.xlsx",
    "sheets": [
        {"sheet": "GENERAL", "header_row": 1, "scope": "job",
         "columns": [{"column": "A", "header": "Consignee", "field": "consignee_full_name"}]},
    ],
}


def _make_group(db_session, tenant, *, excel=True):
    group = TemplateGroup(
        tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import",
        entry_mode="excel" if excel else "fields",
        excel_config=EXCEL_CONFIG if excel else None,
    )
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kw):
    kw.setdefault("reference", "JOB-EXCEL")
    kw.setdefault("status", "extracted")
    kw.setdefault("gk2_status", "pending")
    kw.setdefault("gk2_validation_approved", True)
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_gk2(db_session, tenant, modes):
    user = make_user(db_session, role="gk2", tenant=tenant, email="gk2-excel@example.com")
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_erp_script(db_session, tenant, group, *, steps=None):
    script = ErpScript(
        tenant_id=tenant.id, name="Softlink (Excel)", url="https://erp.example.com/import",
        has_login=False, template_ids=[group.id], status="ready",
        steps=steps if steps is not None else [
            {"action": "upload", "selector": "#file", "value": "excel_import"},
            {"action": "click", "selector": "#submit"},
        ],
    )
    db_session.add(script)
    db_session.commit()
    db_session.refresh(script)
    return script


def test_gk2_approve_clears_the_last_runs_result_so_the_banner_cant_go_stale(client, db_session):
    """A screenshot caught the badge saying "ERP Entry Process Started" while the "ERP replay
    result" banner still said "Entered into the ERP and submitted" underneath it - the banner
    is gated on job.erp_status alone, and a NEW attempt starting must clear the OLD one's
    result immediately, not leave it sitting there until the new attempt finishes."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(
        db_session, tenant, group,
        erp_status="ok", erp_reason=None,  # leftover from a PREVIOUS successful run
    )

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["erp_status"] is None

    db_session.refresh(job)
    assert job.erp_status is None
    assert job.gk2_status == "preparing_erp"


def test_gk2_approve_moves_to_preparing_erp_immediately(client, db_session):
    """The synchronous, in-request part: whatever the eventual outcome, the status must
    visibly change to "in progress" the moment GK2 presses the button - not sit on "pending"
    until a background thread happens to finish."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] != "failed"

    db_session.refresh(job)
    assert job.gk2_status == "preparing_erp"


def test_gk2_approve_parks_in_irn_document_process_when_gk1_chose_approval(client, db_session):
    """GK1 pressing Approval for IRN (not Skip) on IRN Documents Upload means Final Approve &
    Proceed must NOT run the real ERP submission - it parks the job in a new wait-state
    instead, with nothing real touched (erp_status stays untouched, no background thread)."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, irn_approval_requested=True)

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_status"] == "irn_document_process"
    assert resp.json()["outer_status"] == "IRN Document Process"
    assert resp.json()["erp_status"] is None

    db_session.refresh(job)
    assert job.gk2_status == "irn_document_process"
    assert job.erp_status is None


def test_gk2_approve_runs_the_real_submission_when_gk1_chose_skip(client, db_session):
    """The opposite of the test above, and the default for every job from before this column
    existed (irn_approval_requested defaults to False) - Skip changes nothing about the real
    ERP submission path."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, irn_approval_requested=False)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["gk2_status"] == "preparing_erp"

    db_session.refresh(job)
    assert job.gk2_status == "preparing_erp"


def test_run_gk2_submission_runs_the_real_erp_script_and_marks_submitted(db_session):
    """This is complete_job's Submit Entry, not a placeholder: the workbook is built, the
    tenant's ready ErpScript is replayed with it attached, and only a real successful run
    marks the job submitted."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    with patch("app.core.browser.play_steps") as mock_play:
        # "ok" is the real status play_steps reports for a clean run - the same value
        # complete_job's own Submit Entry gets back and the screen already renders for.
        mock_play.return_value = {"status": "ok", "log": ["logged in", "uploaded", "submitted"]}
        _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    assert mock_play.called
    db_session.refresh(job)
    assert job.gk2_status == "submitted"
    assert job.erp_status == "ok"
    assert job.status != "failed"


def test_run_gk2_submission_sends_a_failed_build_to_failed_for_a_retry(db_session):
    """Not a dead end: GK2 sees "Failed" plus why (erp_status/erp_reason) and simply presses
    Final Approve & Proceed again themselves, once whatever caused it is fixed - no GK1 or
    admin step needed to unstick it."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    # No JobFieldValue at all - the one mapped column has nothing to fill it with.

    _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    db_session.refresh(job)
    assert job.status != "failed"
    assert job.erp_status == "error"
    assert job.gk2_status == "failed"
    assert job.erp_reason and "empty" in job.erp_reason


def test_run_gk2_submission_fails_when_no_erp_script_is_configured(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    # No ErpScript at all for this tenant/template.
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    db_session.refresh(job)
    assert job.gk2_status == "failed"
    assert job.status != "failed"
    assert job.erp_reason and "No ready ERP script" in job.erp_reason


def test_run_gk2_submission_fails_when_the_script_has_no_upload_step_for_the_workbook(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group, steps=[{"action": "click", "selector": "#submit"}])
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    with patch("app.core.browser.play_steps") as mock_play:
        _run_gk2_submission(db_session, job.id, is_excel_entry=True)
        assert not mock_play.called  # refused before ever touching the ERP

    db_session.refresh(job)
    assert job.gk2_status == "failed"
    assert job.status != "failed"
    assert job.erp_reason and "no upload step" in job.erp_reason


def test_run_gk2_submission_shows_entering_erp_while_the_real_script_runs(db_session):
    """The moment between the workbook being ready and play_steps starting is when the badge
    must already say "ERP Entry Process Started" - checked by having the mocked play_steps
    itself look at the job's current gk2_status, the way a concurrent poll would observe the
    real, several-minute run."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    seen = {}

    def fake_play_steps(*args, **kwargs):
        db_session.refresh(job)
        seen["gk2_status"] = job.gk2_status
        return {"status": "ok", "log": ["ok"]}

    with patch("app.core.browser.play_steps", side_effect=fake_play_steps):
        _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    assert seen["gk2_status"] == "entering_erp"
    db_session.refresh(job)
    assert job.gk2_status == "submitted"  # lands on the real outcome once the run returns


def test_run_gk2_submission_fails_when_the_real_erp_run_fails(db_session):
    """The build can succeed and the script can be well-formed, and the ERP itself can still
    reject the run - that must land GK2 on "Failed" with the real reason, not a false
    "submitted"."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    with patch("app.core.browser.play_steps") as mock_play:
        mock_play.return_value = {
            "status": "error", "reason": "The ERP rejected the AD Code.",
            "log": ["logged in", "uploaded", "rejected"],
        }
        _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    db_session.refresh(job)
    assert job.gk2_status == "failed"
    assert job.status != "failed"
    assert job.erp_reason == "The ERP rejected the AD Code."


def test_run_gk2_submission_non_excel_template_marks_submitted(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, excel=False)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")

    _run_gk2_submission(db_session, job.id, is_excel_entry=False)

    db_session.refresh(job)
    assert job.gk2_status == "submitted"
    assert job.status != "failed"


def test_gk2_can_retry_after_a_failed_submission_without_help_from_gk1(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp")
    # First attempt fails - nothing mapped yet.
    _run_gk2_submission(db_session, job.id, is_excel_entry=True)
    db_session.refresh(job)
    assert job.gk2_status == "failed"

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    # GK2 presses the button again, no GK1/admin step needed in between - gk2_approve accepts
    # "failed" the same way it accepts "pending".
    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 200, resp.text
    db_session.refresh(job)
    assert job.gk2_status == "preparing_erp"


def test_job_detail_maps_the_new_gk2_states_to_the_right_outer_status(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    entering = _make_job(db_session, tenant, group, gk2_status="entering_erp", reference="JOB-ENTERING")
    failed = _make_job(db_session, tenant, group, gk2_status="failed", reference="JOB-FAILED")

    op = make_user(db_session, role="operator", tenant=tenant, email="op-outer-status@example.com")
    login(client, op.email)

    entering_detail = client.get(f"/api/v1/jobs/{entering.id}").json()
    assert entering_detail["outer_status"] == "ERP Entry Process Started"
    assert entering_detail["gk2_status"] == "entering_erp"

    failed_detail = client.get(f"/api/v1/jobs/{failed.id}").json()
    assert failed_detail["outer_status"] == "Failed"
    assert failed_detail["gk2_status"] == "failed"


def test_run_gk2_submission_clears_a_stale_failed_status_on_success(db_session):
    """A job left with status="failed" from before the retry-friendly failure behaviour
    existed (deploy69) must not keep showing failed forever once a later attempt actually
    succeeds - the badge (gk2_status/erp_status) and job.status must agree. A real successful
    run then sets job.status the same way complete_job's Submit Entry does (via
    persist_run_outcome) - "completed", not the stale "failed" it started at."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_erp_script(db_session, tenant, group)
    job = _make_job(db_session, tenant, group, gk2_status="preparing_erp", status="failed")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    with patch("app.core.browser.play_steps") as mock_play:
        mock_play.return_value = {"status": "ok", "log": ["ok"]}
        _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.gk2_status == "submitted"


def test_super_admin_can_reopen_a_gk2_submitted_job(client, db_session):
    """A job that finished under the OLD placeholder approval (status "completed",
    gk2_status "submitted", nothing real ever ran) needs a real way back to Pending GK2
    Approval for an actual re-submission - not a one-off database edit."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="submitted", status="completed")

    admin = make_user(db_session, role="super_admin", email="admin-reopen@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/reopen")
    assert resp.status_code == 200, resp.text

    db_session.refresh(job)
    assert job.status == "extracted"
    assert job.gk2_status == "pending"


def test_reopen_rejects_a_job_that_was_never_submitted_to_gk2(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="pending")

    admin = make_user(db_session, role="super_admin", email="admin-reopen2@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/reopen")
    assert resp.status_code == 409


def test_reopen_is_not_available_to_gk2_itself(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="submitted", status="completed")

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/reopen")
    assert resp.status_code == 403


def test_sync_status_fixes_a_stale_failed_gk2_status_on_a_completed_job(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="failed", status="completed")

    admin = make_user(db_session, role="super_admin", email="admin-sync1@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/sync-status")
    assert resp.status_code == 200, resp.text

    db_session.refresh(job)
    assert job.gk2_status == "submitted"


def test_sync_status_also_fixes_a_stale_in_flight_gk2_status_on_a_completed_job(client, db_session):
    """The real incident this was broadened for: a job that finished through the Entry Browser's
    own live rerun (not gk2_approve's background thread) can be genuinely completed while
    gk2_status is still sitting on whatever in-flight value the run happened to be at when that
    path took over - "entering_erp" here, not just "failed"."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="entering_erp", status="completed")

    admin = make_user(db_session, role="super_admin", email="admin-sync2@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/sync-status")
    assert resp.status_code == 200, resp.text

    db_session.refresh(job)
    assert job.gk2_status == "submitted"


def test_sync_status_rejects_a_job_that_has_not_actually_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="entering_erp", status="extracted")

    admin = make_user(db_session, role="super_admin", email="admin-sync3@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/sync-status")
    assert resp.status_code == 409


def test_sync_status_rejects_a_job_already_correctly_submitted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="submitted", status="completed")

    admin = make_user(db_session, role="super_admin", email="admin-sync4@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/sync-status")
    assert resp.status_code == 409


def test_run_gk2_submission_does_nothing_if_the_job_has_already_moved_on(db_session):
    """A second, racing call (or a stray background thread from an unrelated test run) must
    never overwrite a job that isn't sitting in "preparing_erp" any more."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, gk2_status="submitted")

    _run_gk2_submission(db_session, job.id, is_excel_entry=True)

    db_session.refresh(job)
    assert job.gk2_status == "submitted"


def test_download_as_excel_returns_a_workbook(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="consignee_full_name",
                                 extracted_value="ACME IMPORTS PVT LTD"))
    db_session.commit()

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-excel")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_download_as_excel_reports_a_clear_error_when_the_workbook_would_be_empty(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    gk2 = _make_gk2(db_session, tenant, modes=["Sea Import"])
    login(client, gk2.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-excel")
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"]


def test_download_as_excel_rejects_a_job_not_set_up_for_excel_entry(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, excel=False)
    job = _make_job(db_session, tenant, group)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-excel@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-excel")
    assert resp.status_code == 422


def test_job_detail_flags_excel_entry_so_the_screen_knows_to_offer_the_button(client, db_session):
    tenant = make_tenant(db_session)
    excel_group = _make_group(db_session, tenant, excel=True)
    excel_job = _make_job(db_session, tenant, excel_group)
    fields_group = _make_group(db_session, tenant, excel=False)
    fields_job = _make_job(db_session, tenant, fields_group, reference="JOB-FIELDS")

    op = make_user(db_session, role="operator", tenant=tenant, email="op-flag@example.com")
    login(client, op.email)

    assert client.get(f"/api/v1/jobs/{excel_job.id}").json()["excel_entry"] is True
    assert client.get(f"/api/v1/jobs/{fields_job.id}").json()["excel_entry"] is False

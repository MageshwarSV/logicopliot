"""The "Hold" job status was removed entirely. A template's conditional document ruling (e.g.
"an FOB shipment needs a Freight Certificate, a CIF one doesn't") is now checked at the moment
of submission instead - Submit for Approval / Final Approve & Proceed / Submit Entry all refuse
outright (409) until the ruling can be evaluated AND every document it names as required has
actually been uploaded, rather than parking the job on a separate status beforehand.

See _check_ruling_before_submit (app/api/v1/jobs.py) and Job.ruling_answer, which persists the
operator's own answer (given via the existing "Check required documents" box,
evaluate_job_ruling) so a later submission attempt reuses it instead of asking again."""
from unittest.mock import patch

from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

RULING_PROMPT = "Decide whether a Freight Certificate is required for this shipment."


def _make_group_with_ruling(db_session, tenant, *, ruling=RULING_PROMPT):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved",
                          mode="Sea Import", ruling_prompt=ruling)
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
    kw.setdefault("reference", "JOB-RULING")
    kw.setdefault("status", "extracted")
    kw.setdefault("validation_approved", True)
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    for i, tdoc in enumerate(uploaded_docs):
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                                   file_path="fake.pdf", page_count=1, file_index=0))
    db_session.commit()
    return job


def _login_operator(client, db_session, tenant):
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ruling@example.com")
    op.assigned_modes = ["Sea Import"]
    db_session.add(op)
    db_session.commit()
    login(client, op.email)
    return op


def test_submit_for_approval_blocked_when_the_ruling_needs_input(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": True, "question": "Which incoterm applies to this shipment?",
            "reason": "No real incoterm found in the Bill of Lading, Invoice, or Packing List.",
            "required_documents": [], "decision": None,
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    assert resp.status_code == 409, resp.text
    assert "Cannot submit this job for approval" in resp.json()["detail"]
    assert "incoterm" in resp.json()["detail"].lower()
    db_session.refresh(job)
    assert job.gk2_status is None  # never handed off to GK2


def test_submit_for_approval_blocked_when_a_ruling_required_document_is_missing(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])  # Freight Certificate not uploaded
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": False, "decision": "FOB",
            "required_documents": ["Invoice", "Bill of Lading", "Freight Certificate"],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    assert resp.status_code == 409, resp.text
    assert "Freight Certificate" in resp.json()["detail"]
    db_session.refresh(job)
    assert job.gk2_status is None


def test_submit_for_approval_succeeds_once_the_rulings_requirements_are_met(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl, freight])
    _login_operator(client, db_session, tenant)

    # Fake JobDocument rows carry no real, OCR-able file - _ruling_context's own "uploaded"
    # list is built from actual extracted text, so it is stubbed directly here rather than
    # relying on OCR to somehow read something out of a file that doesn't really exist.
    fake_context = (
        ["Invoice", "Bill of Lading", "Freight Certificate"],
        ["Invoice", "Bill of Lading", "Freight Certificate"],
        "...",
    )
    with patch("app.api.v1.jobs._ruling_context", return_value=fake_context), \
         patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": False, "decision": "FOB",
            "required_documents": ["Invoice", "Bill of Lading", "Freight Certificate"],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    assert resp.status_code == 200, resp.text
    db_session.refresh(job)
    assert job.gk2_status == "pending"


def test_a_previously_given_ruling_answer_is_reused_at_submission(client, db_session):
    """The operator already answered the question via "Check required documents" earlier - a
    later Submit attempt must not ask again, and must pass that same answer through."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl], ruling_answer="CIF")
    _login_operator(client, db_session, tenant)

    fake_context = (["Invoice", "Bill of Lading", "Freight Certificate"], ["Invoice", "Bill of Lading"], "...")
    with patch("app.api.v1.jobs._ruling_context", return_value=fake_context), \
         patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": False, "decision": "CIF",
            "required_documents": ["Invoice", "Bill of Lading"],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
        assert resp.status_code == 200, resp.text
        passed_answer = mock_rule.call_args.args[-1]
        assert passed_answer == "CIF"


def test_a_technical_failure_evaluating_the_ruling_blocks_submission(client, db_session):
    """A real error (the AI call itself fails) must never be silently skipped - that would
    submit on exactly as much of a guess as ignoring a genuine needs_input would."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling", side_effect=RuntimeError("openai timeout")):
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    assert resp.status_code == 409, resp.text
    assert "technical error" in resp.json()["detail"].lower()
    db_session.refresh(job)
    assert job.gk2_status is None


def test_a_template_with_no_ruling_configured_is_unaffected(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant, ruling=None)
    job = _make_job(db_session, tenant, group, [invoice, bl])  # Freight Certificate never uploaded
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    assert resp.status_code == 200, resp.text
    assert not mock_rule.called
    db_session.refresh(job)
    assert job.gk2_status == "pending"


def test_evaluate_job_ruling_persists_the_answer_without_setting_any_stage_override(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": True, "question": "Which incoterm applies?",
            "reason": "no incoterm found", "required_documents": [],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/ruling", json={"answer": "CIF"})

    assert resp.status_code == 200, resp.text
    assert "held" not in resp.json()
    db_session.refresh(job)
    assert job.ruling_answer == "CIF"
    assert job.stage_override is None


def test_gk2_final_approve_is_also_blocked_when_the_ruling_needs_input(client, db_session):
    """Same gate, reached through the OTHER real submission path - GK2's own Final Approve &
    Proceed, for a tenant/mode where GK2 review is actually required (_finalize_job_approval is
    shared by both)."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl], gk2_status="pending",
                    gk2_validation_approved=True)
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-ruling@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": True, "question": "Which incoterm applies?",
            "reason": "no incoterm found", "required_documents": [],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")

    assert resp.status_code == 409, resp.text
    assert "Cannot submit this job for approval" in resp.json()["detail"]
    db_session.refresh(job)
    assert job.gk2_status == "pending"  # never moved to preparing_erp


def test_submit_entry_is_also_blocked_when_the_ruling_needs_input(client, db_session):
    """Same gate, reached through the direct (non-GK2) Submit Entry path."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])
    op = _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {
            "needs_input": True, "question": "Which incoterm applies?",
            "reason": "no incoterm found", "required_documents": [],
        }
        resp = client.post(f"/api/v1/jobs/{job.id}/complete")

    assert resp.status_code == 409, resp.text
    assert "Cannot submit this job for approval" in resp.json()["detail"]
    db_session.refresh(job)
    assert job.status == "extracted"  # never touched


def test_clear_stale_hold_resets_an_old_jobs_stage_override(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl], stage_override="Hold")
    admin = make_user(db_session, role="super_admin", email="admin-clear-hold@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/clear-stale-hold")
    assert resp.status_code == 200, resp.text
    db_session.refresh(job)
    assert job.stage_override is None


def test_clear_stale_hold_refuses_a_job_not_actually_on_a_stale_hold(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl], stage_override="ERP Submission")
    admin = make_user(db_session, role="super_admin", email="admin-clear-hold2@example.com")
    login(client, admin.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/clear-stale-hold")
    assert resp.status_code == 409
    db_session.refresh(job)
    assert job.stage_override == "ERP Submission"  # untouched


def test_clear_stale_hold_is_refused_to_a_non_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl], stage_override="Hold")
    _login_operator(client, db_session, tenant)

    resp = client.post(f"/api/v1/jobs/{job.id}/clear-stale-hold")
    assert resp.status_code == 403


def test_outer_status_never_shows_hold_even_when_the_ruling_needs_input(client, db_session):
    """The whole point: a job the ruling can't resolve stays wherever it already genuinely is
    in its own pipeline - it is never parked on a separate "Hold" status."""
    tenant = make_tenant(db_session)
    group, invoice, bl, freight = _make_group_with_ruling(db_session, tenant)
    job = _make_job(db_session, tenant, group, [invoice, bl])
    _login_operator(client, db_session, tenant)

    with patch("app.core.llm.evaluate_ruling") as mock_rule:
        mock_rule.return_value = {"needs_input": True, "question": "?", "reason": "?", "required_documents": []}
        client.post(f"/api/v1/jobs/{job.id}/ruling", json={})
        client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["outer_status"] != "Hold"

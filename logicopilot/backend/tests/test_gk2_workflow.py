"""The GK1 (operator) -> GK2 approval chain: _outer_status's new branches, the two new
gk2/* endpoints, GK2's filtered job queue, and Tenant Admin creating a GK2 user."""
from unittest.mock import patch

from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode=None):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_group_with_required_doc(db_session, tenant):
    """A template group with no document slots at all vacuously has "every required document
    already present" - real templates always require at least one, so a test asserting
    "still waiting on documents" needs one too, or it is testing a setup that cannot occur."""
    group = _make_group(db_session, tenant)
    db_session.add(TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                                    doc_type="Invoice", is_required=True))
    db_session.commit()
    return group


def _make_job(db_session, tenant, group, *, status="extracted", created_by_id=None,
              validation_approved=False, gk2_status=None, gk2_validation_approved=False):
    job = Job(
        tenant_id=tenant.id, group_id=group.id, reference="JOB-TEST",
        status=status, created_by_id=created_by_id,
        validation_approved=validation_approved, gk2_status=gk2_status,
        gk2_validation_approved=gk2_validation_approved,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_gk2(db_session, tenant, modes, email=None):
    user = make_user(db_session, role="gk2", tenant=tenant, email=email)
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ---------------------------------------------------------------- _outer_status branches


def test_pre_alert_received_for_an_email_created_job(client, db_session):
    """Still missing its one required document - an email pre-alert with nothing uploaded
    yet reads as "Pre-Alert Received", not the operator-facing "Pending Documents"."""
    tenant = make_tenant(db_session)
    group = _make_group_with_required_doc(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="draft", created_by_id=None)
    admin = make_user(db_session, role="super_admin", email="sa-outer1@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "Pre-Alert Received"


def test_pending_documents_for_a_manually_created_job(client, db_session):
    """Still missing its one required document - a manually created job with nothing
    uploaded yet reads as "Pending Documents"."""
    tenant = make_tenant(db_session)
    group = _make_group_with_required_doc(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-outer1@example.com")
    job = _make_job(db_session, tenant, group, status="draft", created_by_id=op.id)
    admin = make_user(db_session, role="super_admin", email="sa-outer2@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "Pending Documents"


def test_document_capture_once_every_required_document_is_present(client, db_session):
    """A template with no document requirements at all has nothing to wait for - "Document
    Capture" the moment it exists, same as a real job whose one required slot is filled."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)  # no TemplateDocument rows - nothing required
    op = make_user(db_session, role="operator", tenant=tenant, email="op-outer3@example.com")
    job = _make_job(db_session, tenant, group, status="draft", created_by_id=op.id)
    admin = make_user(db_session, role="super_admin", email="sa-outer3@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "Document Capture"


def test_ai_processing_while_extracting(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="extracting")
    admin = make_user(db_session, role="super_admin", email="sa-outer3@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "AI - Processing"


def test_gk1_review_then_gk1_reviewing(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="extracted")
    admin = make_user(db_session, role="super_admin", email="sa-outer4@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "GK1 Review"

    job.validation_approved = True
    from app.models.job import JobFieldValue
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="X", corrected_value="Y",
    ))
    db_session.commit()

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "GK1 Reviewing"


def test_gk2_status_branches_win_over_the_rail(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-outer5@example.com")
    login(client, admin.email)

    for gk2_status, expected in (
        ("pending", "Pending GK2 Approval"),
        ("preparing_erp", "AI - Preparing for ERP"),
        ("submitted", "AI - ERP Submitted"),
    ):
        job = _make_job(db_session, tenant, group, status="extracted", gk2_status=gk2_status)
        resp = client.get("/api/v1/jobs")
        row = next(j for j in resp.json() if j["id"] == job.id)
        assert row["outer_status"] == expected


# ---------------------------------------------------------------- submit-for-approval


def test_operator_submits_for_gk2_approval(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-submit1@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id,
                    validation_approved=True)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 200, resp.text
    assert resp.json()["outer_status"] == "Pending GK2 Approval"
    db_session.refresh(job)
    assert job.gk2_status == "pending"


def test_submit_for_gk2_approval_refused_before_validation_approved(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-submit2@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id,
                    validation_approved=False)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 409


def test_gk2_user_cannot_submit_for_approval(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-submit@example.com")
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 403


def test_approve_validation_is_unconditional(client, db_session):
    """The operator's "Approved and Proceed" is a deliberate override now - it no longer
    inspects cross-check findings at all before recording the approval (previously refused
    with a 400 while a Mismatch/Review was open)."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-override@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/validation/approve")
    assert resp.status_code == 200, resp.text
    db_session.refresh(job)
    assert job.validation_approved is True


# ---------------------------------------------------------------- gk2 approve


def test_gk2_approve_moves_to_preparing_then_submitted(client, db_session, session_factory):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
                    gk2_status="pending", gk2_validation_approved=True)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-approve1@example.com")
    login(client, gk2.email)

    # The background thread opens its OWN app.db.session.SessionLocal() — point that at the
    # same in-memory test database this test itself uses, not the real one, and skip the
    # actual 5s wait.
    with patch("time.sleep", return_value=None), \
         patch("app.db.session.SessionLocal", session_factory):
        resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
        assert resp.status_code == 200, resp.text
        assert resp.json()["outer_status"] == "AI - Preparing for ERP"
        import time as _time
        for _ in range(20):
            db_session.expire_all()
            fresh = db_session.get(Job, job.id)
            if fresh.gk2_status == "submitted":
                break
            _time.sleep(0.05)
        else:
            raise AssertionError("gk2_status never reached 'submitted'")


def test_gk2_approve_refused_when_not_pending(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
                    gk2_status=None)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-approve2@example.com")
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 409


def test_gk2_approve_refused_before_gk2s_own_validation_approval(client, db_session):
    """GK2 must independently approve Data Validation before their own Final Approve &
    Proceed unlocks - GK1 having approved it is not enough."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
                    gk2_status="pending", gk2_validation_approved=False)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-noselfapproval@example.com")
    login(client, gk2.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 409


def test_gk1_and_gk2_document_approvals_are_independent(client, db_session):
    """GK1 approving a document must not make it look approved to GK2, and vice versa -
    they are two separate sign-offs on the same job."""
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    op = make_user(db_session, role="operator", tenant=tenant, email="op-independent@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id)
    jdoc = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path="/tmp/x.pdf", page_count=1)
    db_session.add(jdoc)
    db_session.commit()
    db_session.refresh(jdoc)

    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-independent@example.com")

    # GK1 approves the document.
    login(client, op.email)
    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{jdoc.id}/approve", json={"approved": True})
    assert resp.status_code == 200, resp.text
    gk1_view = next(d for d in resp.json()["documents"] if d["id"] == jdoc.id)
    assert gk1_view["approved"] is True
    assert gk1_view["gk2_approved"] is False  # GK2 has not touched it

    # GK2 opens the SAME job and must see it as unapproved from their own side.
    login(client, gk2.email)
    resp = client.get(f"/api/v1/jobs/{job.id}")
    gk2_view = next(d for d in resp.json()["documents"] if d["id"] == jdoc.id)
    assert gk2_view["approved"] is True       # GK1's own field, unaffected by GK2
    assert gk2_view["gk2_approved"] is False  # GK2's own field, still unapproved

    # GK2 approves it independently.
    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{jdoc.id}/approve", json={"approved": True})
    assert resp.status_code == 200, resp.text
    both_view = next(d for d in resp.json()["documents"] if d["id"] == jdoc.id)
    assert both_view["approved"] is True
    assert both_view["gk2_approved"] is True

    db_session.refresh(jdoc)
    assert jdoc.approved is True
    assert jdoc.gk2_approved is True


def test_gk1_and_gk2_validation_approvals_are_independent(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    op = make_user(db_session, role="operator", tenant=tenant, email="op-vindependent@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=op.id)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-vindependent@example.com")

    login(client, op.email)
    resp = client.post(f"/api/v1/jobs/{job.id}/validation/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["validation_approved"] is True
    assert resp.json()["gk2_validation_approved"] is False

    login(client, gk2.email)
    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.json()["validation_approved"] is True        # GK1's, unaffected
    assert resp.json()["gk2_validation_approved"] is False    # GK2 has not approved yet

    resp = client.post(f"/api/v1/jobs/{job.id}/validation/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["validation_approved"] is True
    assert resp.json()["gk2_validation_approved"] is True

    db_session.refresh(job)
    assert job.validation_approved is True
    assert job.gk2_validation_approved is True


def test_operator_cannot_gk2_approve(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-approve@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
                    gk2_status="pending", created_by_id=op.id)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/approve")
    assert resp.status_code == 403


# ---------------------------------------------------------------- GK2's filtered job list


def test_gk2_sees_only_pending_jobs_in_their_own_modes(client, db_session):
    tenant = make_tenant(db_session)
    sea_group = _make_group(db_session, tenant, mode="Sea Import")
    air_group = _make_group(db_session, tenant, mode="Air Import")

    sea_pending = _make_job(db_session, tenant, sea_group, status="extracted",
                            validation_approved=True, gk2_status="pending")
    air_pending = _make_job(db_session, tenant, air_group, status="extracted",
                            validation_approved=True, gk2_status="pending")
    sea_not_yet = _make_job(db_session, tenant, sea_group, status="extracted",
                            validation_approved=True, gk2_status=None)

    sea_gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-sea@example.com")
    login(client, sea_gk2.email)
    ids = {j["id"] for j in client.get("/api/v1/jobs").json()}
    assert ids == {sea_pending.id}
    assert air_pending.id not in ids
    assert sea_not_yet.id not in ids


def test_gk2_with_no_modes_sees_nothing(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
             gk2_status="pending")
    gk2 = _make_gk2(db_session, tenant, None, email="gk2-nomodes@example.com")
    login(client, gk2.email)

    assert client.get("/api/v1/jobs").json() == []


def test_gk2_list_still_shows_jobs_they_already_approved(client, db_session):
    """A job must not vanish from GK2's own list the moment they act on it - only a job
    still with an operator (gk2_status still null) is excluded."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    pending = _make_job(db_session, tenant, group, status="extracted",
                        validation_approved=True, gk2_status="pending")
    preparing = _make_job(db_session, tenant, group, status="extracted",
                          validation_approved=True, gk2_status="preparing_erp")
    submitted = _make_job(db_session, tenant, group, status="extracted",
                          validation_approved=True, gk2_status="submitted")
    not_yet_theirs = _make_job(db_session, tenant, group, status="extracted",
                               validation_approved=True, gk2_status=None)

    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-history@example.com")
    login(client, gk2.email)
    ids = {j["id"] for j in client.get("/api/v1/jobs").json()}
    assert ids == {pending.id, preparing.id, submitted.id}
    assert not_yet_theirs.id not in ids


# ---------------------------------------------------------------- Tenant Admin creates GK2


def test_tenant_admin_creates_gk2_user(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-creates-gk2@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-gk2@example.com", "password": "TestPass123", "full_name": "New GK2",
        "role": "gk2", "modes": ["Sea Import", "Air Import"],
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "gk2"
    assert sorted(body["modes"]) == ["Air Import", "Sea Import"]


def test_tenant_admin_creating_gk2_without_modes_is_refused(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-gk2-nomodes@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-gk2-2@example.com", "password": "TestPass123", "full_name": "New GK2",
        "role": "gk2",
    })
    assert resp.status_code == 400


def test_gk2_user_can_open_a_job_they_are_reviewing(client, db_session):
    """Regression: opening a job from the "Pending GK2 Approval" queue 403'd on the plain
    GET /jobs/{id} call, showing "Failed to load the job." for every GK2 user."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group, status="extracted", validation_approved=True,
                    gk2_status="pending")
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-open@example.com")
    login(client, gk2.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["outer_status"] == "Pending GK2 Approval"


def test_gk2_user_can_call_available_groups_without_403(client, db_session):
    """Regression: JobsPage loads jobs and available-groups in one Promise.all, so a 403
    on either one failed the WHOLE jobs page for GK2 with "Failed to load jobs"."""
    tenant = make_tenant(db_session)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-groups@example.com")
    login(client, gk2.email)

    resp = client.get("/api/v1/available-groups")
    assert resp.status_code == 200, resp.text


def test_completed_job_shows_as_ai_erp_submitted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="completed")
    admin = make_user(db_session, role="super_admin", email="sa-completed@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/jobs")
    row = next(j for j in resp.json() if j["id"] == job.id)
    assert row["outer_status"] == "AI - ERP Submitted"


def test_gk2_outside_their_modes_cannot_view_or_act_on_a_job(client, db_session):
    """assigned_modes is the REAL access gate, not just a list filter - a GK2 user restricted
    to Sea Import must not be able to view or approve an Air Import job even by direct id,
    the same 404 an operator gets for someone else's job."""
    tenant = make_tenant(db_session)
    air_group = _make_group(db_session, tenant, mode="Air Import")
    job = _make_job(db_session, tenant, air_group, status="extracted", validation_approved=True,
                    gk2_status="pending", gk2_validation_approved=True)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-outsidemodes@example.com")
    login(client, gk2.email)

    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 404
    assert client.post(f"/api/v1/jobs/{job.id}/validation/approve").status_code == 404
    assert client.post(f"/api/v1/jobs/{job.id}/gk2/approve").status_code == 404


def test_gk2_outside_their_modes_cannot_correct_a_field_value(client, db_session):
    """A value on its own carries no mode - only its job does. correct_field_value used to
    skip loading the job entirely, so this restriction was bypassable by id alone."""
    from app.models.job import JobFieldValue

    tenant = make_tenant(db_session)
    air_group = _make_group(db_session, tenant, mode="Air Import")
    job = _make_job(db_session, tenant, air_group, status="extracted")
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="X", extracted_value="Y")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    gk2 = _make_gk2(db_session, tenant, ["Sea Import"], email="gk2-outsidemodes-fv@example.com")
    login(client, gk2.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "Z"})
    assert resp.status_code == 404


def test_operator_cannot_correct_another_operators_field_value(client, db_session):
    """Same gap, operator side: a value carries no owner of its own - only its job does."""
    from app.models.job import JobFieldValue

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    owner = make_user(db_session, role="operator", tenant=tenant, email="op-fvowner@example.com")
    other = make_user(db_session, role="operator", tenant=tenant, email="op-fvother@example.com")
    job = _make_job(db_session, tenant, group, status="extracted", created_by_id=owner.id)
    job.assigned_operator_id = owner.id
    db_session.add(job)
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="X", extracted_value="Y")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    login(client, other.email)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "Z"})
    assert resp.status_code == 404


def test_gk2_creation_rejects_an_invalid_mode(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-gk2-badmode@example.com")
    login(client, ta.email)

    resp = client.post("/api/v1/users", json={
        "email": "new-gk2-3@example.com", "password": "TestPass123", "full_name": "New GK2",
        "role": "gk2", "modes": ["Not A Real Mode"],
    })
    assert resp.status_code == 422

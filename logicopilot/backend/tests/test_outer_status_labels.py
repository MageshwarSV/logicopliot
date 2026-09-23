"""_outer_status() display-label translation for the two renamed rail stages. The internal
stage words themselves ("Data Extraction", "Data Validation") must never change - they are
stored verbatim in job_events.stage and compared throughout _job_stage() - only what gets
shown to a user should differ. See _OPERATOR_STAGES and _begin_extraction() for why."""
from app.api.v1.jobs import _outer_status
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job_at_stage(db_session, tenant, group, stage: str):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LABEL", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(JobEvent(tenant_id=tenant.id, job_id=job.id, status="extracted",
                            stage=stage, note="moved on"))
    db_session.commit()
    return job


def test_data_extraction_stage_shows_gk1_review(db_session):
    """Previously there was no branch for this stage at all - it leaked the raw internal
    word "Data Extraction" straight through to the job list and header. GK1's whole working
    phase (Required Details Review AND Cross Docs Verification) reads as the same
    "GK1 Review" pair Data Validation already used, not a third, different-sounding label."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job_at_stage(db_session, tenant, group, "Data Extraction")
    assert _outer_status(db_session, job) == "GK1 Review"


def test_data_extraction_stage_shows_gk1_reviewing_once_a_document_is_approved(db_session):
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    job = _make_job_at_stage(db_session, tenant, group, "Data Extraction")
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake.pdf", page_count=1, approved=True)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(job)

    assert _outer_status(db_session, job) == "GK1 Reviewing"


def test_pending_documents_with_one_of_two_required_uploaded(db_session):
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                              doc_type="Invoice", is_required=True)
    packing_list = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                                    doc_type="Packing List", is_required=True)
    db_session.add_all([invoice, packing_list])
    db_session.commit()
    db_session.refresh(invoice)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-pending-docs@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PARTIAL",
             status="draft", created_by_id=op.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    # Only the invoice has arrived - the packing list slot is still empty.
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=invoice.id,
                     file_path="fake.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(job)

    assert _outer_status(db_session, job) == "Pending Documents"


def test_document_capture_once_a_previously_extracted_job_reverts_to_draft(db_session):
    """A job whose background extraction crashed reverts to "draft" with its documents
    already uploaded (see _start_extraction_background's except branch) - it must read as
    "Document Capture" (ready to try again), not "Pending Documents" (still needs paperwork
    it already has)."""
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice", is_required=True)
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-capture-done@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REVERTED",
             status="draft", created_by_id=op.id)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(job)

    assert _outer_status(db_session, job) == "Document Capture"


def test_data_validation_stage_still_shows_gk1_review_unchanged(db_session):
    """The rail rename does not touch this pre-existing, more informative translation —
    only the raw internal word "Data Validation" itself was ever meant to stop leaking
    through as literal text; this label was already friendly before the rename."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job_at_stage(db_session, tenant, group, "Data Validation")
    assert _outer_status(db_session, job) == "GK1 Review"


def test_gk2_approval_error_uses_the_renamed_label(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-label1@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LABEL2",
             status="extracted", created_by_id=op.id, validation_approved=False)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    login(client, op.email)

    resp = client.post(f"/api/v1/jobs/{job.id}/gk2/submit-for-approval")
    assert resp.status_code == 409
    assert "Cross Docs Verification" in resp.json()["detail"]
    assert "Data Validation" not in resp.json()["detail"]


def test_hold_with_a_required_document_still_missing_shows_pending_documents(db_session):
    """A ruling hold ("which Incoterm applies?") can fire even before every document is in -
    the AI cannot read an Incoterm off a Bill of Lading nobody uploaded yet. The missing
    paperwork is the more fundamental blocker, so it must win over the generic "Hold" label."""
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                              doc_type="Invoice", is_required=True)
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading",
                          doc_type="Bill of Lading", is_required=True)
    db_session.add_all([invoice, bl])
    db_session.commit()
    db_session.refresh(invoice)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-hold-missing@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-HOLD-MISSING",
             status="extracted", created_by_id=op.id, stage_override="Hold")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    # Only the invoice arrived - the Bill of Lading slot is still empty.
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=invoice.id,
                     file_path="fake.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(job)

    assert _outer_status(db_session, job) == "Pending Documents"


def test_hold_with_every_required_document_present_still_shows_hold(db_session):
    """Once every required document is actually uploaded, a ruling hold is a real question for
    the operator (which Incoterm applies?), not a paperwork gap - it must keep its own label."""
    from app.models.job import JobDocument
    from app.models.template_document import TemplateDocument

    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                              doc_type="Invoice", is_required=True)
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)

    op = make_user(db_session, role="operator", tenant=tenant, email="op-hold-complete@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-HOLD-COMPLETE",
             status="extracted", created_by_id=op.id, stage_override="Hold")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=invoice.id,
                     file_path="fake.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(job)

    assert _outer_status(db_session, job) == "Hold"

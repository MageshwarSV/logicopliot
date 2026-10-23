"""POST /jobs/{job_id}/documents/{job_document_id}/copy-to/{template_document_id}

A single uploaded file sometimes genuinely IS two documents at once (a combined BL/Freight
sheet, say) - this lets an already-placed document be put into a SECOND slot too, as its own
independent copy, without re-uploading the same file by hand. The frontend's "Move to X" is
built from this same endpoint plus the existing DELETE .../file endpoint on the source - there
is no separate "move" endpoint."""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _tiny_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def _make_template_two_slots(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="bl")
    freight = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Freight Certificate", doc_type="freight")
    db_session.add_all([bl, freight])
    db_session.commit()
    db_session.refresh(bl)
    db_session.refresh(freight)
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="BL No",
                             page_number=1, x=0.1, y=0.1, width=0.2, height=0.05))
    db_session.commit()
    return group, bl, freight


def _make_job_with_slots(db_session, tenant, group, *slots, status="extracted"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-COPYTEST", status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    for i, tdoc in enumerate(slots):
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                                   file_path=None, page_count=0, file_index=0))
    db_session.commit()
    return job


def _login_operator(client, db_session, tenant):
    op = make_user(db_session, role="operator", tenant=tenant, email="op-copy@example.com")
    login(client, op.email)
    return op


def _upload(client, job_id, tdoc_id):
    return client.post(
        f"/api/v1/jobs/{job_id}/documents/{tdoc_id}/upload",
        files={"file": ("bl.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )


def test_copies_an_already_placed_document_into_a_second_slot(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id)
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/copy-to/{freight.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    # Source is untouched.
    still_there = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert still_there.file_path is not None
    # A new, independent row now exists in the destination slot.
    copies = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).all()
    assert len(copies) == 1
    assert copies[0].file_path is not None
    assert copies[0].id != source.id
    assert copies[0].original_name == source.original_name


def test_move_to_is_copy_then_remove_the_source(client, db_session):
    """The frontend's "Move to X" is just this endpoint followed by the existing Remove
    button's own DELETE call - proven here by calling both directly, the same sequence the UI
    performs."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    copy_resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/copy-to/{freight.id}")
    assert copy_resp.status_code == 200, copy_resp.text
    remove_resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/file")
    assert remove_resp.status_code == 200, remove_resp.text

    db_session.expire_all()
    emptied = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert emptied.file_path is None
    moved = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()
    assert moved.file_path is not None


def test_refuses_to_copy_into_the_same_slot_it_is_already_in(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/copy-to/{bl.id}")
    assert resp.status_code == 422


def test_refuses_when_the_source_document_has_no_file_yet(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    empty_source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{empty_source.id}/copy-to/{freight.id}")
    assert resp.status_code == 404


def test_refuses_a_destination_slot_that_does_not_belong_to_this_job(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl)  # no "freight" slot on this job
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/copy-to/{freight.id}")
    assert resp.status_code == 404


def test_refuses_to_copy_while_the_job_is_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight, status="extracted")
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    job.status = "extracting"
    db_session.commit()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/copy-to/{freight.id}")
    assert resp.status_code == 409

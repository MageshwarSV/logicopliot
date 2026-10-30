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


def _two_page_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page(width=200, height=200)
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


def _tiny_png_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    return page.get_pixmap().tobytes("png")


def _upload(client, job_id, tdoc_id, content: bytes | None = None):
    return client.post(
        f"/api/v1/jobs/{job_id}/documents/{tdoc_id}/upload",
        files={"file": ("bl.pdf", content or _tiny_pdf_bytes(), "application/pdf")},
    )


def _upload_png(client, job_id, tdoc_id):
    return client.post(
        f"/api/v1/jobs/{job_id}/documents/{tdoc_id}/upload",
        files={"file": ("scan.png", _tiny_png_bytes(), "image/png")},
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


# ---------------------------------------------------- per-page copy (a single page out of a
# ---------------------------------------------------- multi-page document, into another slot)


def test_copies_one_page_of_a_multi_page_document_into_another_slot_as_its_own_document(client, db_session):
    """Page 1 is the BL, page 2 is the Freight Certificate - both bound into one uploaded PDF.
    Copying page 2 alone must land in the destination slot as its OWN single-page document,
    and the source must keep every one of its original pages untouched."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    assert source.page_count == 2

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/2/copy-to/{freight.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    still_there = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert still_there.page_count == 2  # the source is untouched, both of its pages intact

    copies = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).all()
    assert len(copies) == 1
    assert copies[0].page_count == 1
    assert copies[0].id != source.id
    assert "page 2" in (copies[0].original_name or "")


def test_refuses_a_page_number_outside_the_documents_own_range(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)  # a single-page document
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/2/copy-to/{freight.id}")
    assert resp.status_code == 404


def test_refuses_a_page_copy_when_the_source_document_has_no_file_yet(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    empty_source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{empty_source.id}/pages/1/copy-to/{freight.id}")
    assert resp.status_code == 404


def test_refuses_a_page_copy_to_a_destination_slot_not_on_this_job(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl)  # no "freight" slot on this job
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/1/copy-to/{freight.id}")
    assert resp.status_code == 404


def test_refuses_a_page_copy_while_the_job_is_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    job.status = "extracting"
    db_session.commit()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/1/copy-to/{freight.id}")
    assert resp.status_code == 409


# ------------------------------------------------- DELETE .../pages/{page_number} ("Move page
# ------------------------------------------------- N" is this, called right after the copy above)


def test_deleting_one_page_of_a_two_page_document_leaves_one_page_behind(client, db_session):
    """"Move page 2 to Freight" on the frontend = copy page 2 there (already tested above),
    then this call to cut page 2 out of the source - leaving the source with exactly its
    remaining page, not the whole document gone."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    assert source.page_count == 2

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/2")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    still_there = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert still_there.page_count == 1
    assert still_there.file_path is not None  # the document itself is still here, just shorter


def test_deleting_the_only_remaining_page_removes_the_whole_document(client, db_session):
    """A 1-page document has no "remaining pages" to rebuild from - cutting its only page IS
    removing the document, same as the existing DELETE .../file endpoint already does."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)  # single page
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    assert source.page_count == 1

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/1")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    emptied = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert emptied.file_path is None
    assert emptied.page_count == 0


def test_refuses_a_page_number_outside_range_for_delete(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/5")
    assert resp.status_code == 404


def test_refuses_to_delete_a_page_while_the_job_is_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    job.status = "extracting"
    db_session.commit()

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/1")
    assert resp.status_code == 409


def test_full_move_a_page_recipe_copy_then_delete(client, db_session):
    """The exact sequence the frontend's "Move page N to X" performs: copy page 2 into
    Freight, then delete page 2 from the source - end state is page 1 alone in BL, page 2
    alone (as its own document) in Freight."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    up = _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    assert up.status_code == 200, up.text
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    copy_resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/2/copy-to/{freight.id}")
    assert copy_resp.status_code == 200, copy_resp.text
    delete_resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/pages/2")
    assert delete_resp.status_code == 200, delete_resp.text

    db_session.expire_all()
    bl_doc = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert bl_doc.page_count == 1

    freight_doc = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()
    assert freight_doc.page_count == 1
    assert freight_doc.id != bl_doc.id


# ------------------------------------------------- merge-with / merge-page (dropping a document
# ------------------------------------------------- or page directly ONTO an existing document)


def test_merging_a_whole_document_appends_its_pages_onto_the_target(client, db_session):
    """Dropping a document directly onto an already-placed one means "this belongs WITH that
    one" - the target grows by the source's own page count, the source is left untouched."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    _upload(client, job.id, freight.id)  # 1-page target
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{target.id}/merge-with/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    merged_target = db_session.query(JobDocument).filter(JobDocument.id == target.id).one()
    assert merged_target.page_count == 3  # 1 original + 2 from the source
    still_there = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert still_there.page_count == 2  # the source itself is untouched


def test_merging_one_page_appends_a_single_page_onto_the_target(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id, content=_two_page_pdf_bytes())
    _upload(client, job.id, freight.id)  # 1-page target
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{target.id}/merge-page/{source.id}/2")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    merged_target = db_session.query(JobDocument).filter(JobDocument.id == target.id).one()
    assert merged_target.page_count == 2  # 1 original + 1 merged page
    still_there = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert still_there.page_count == 2  # the source itself is untouched


def test_full_move_via_merge_recipe_copy_then_delete_the_source(client, db_session):
    """The frontend's "merge by dragging" recipe: merge-with, then remove the source outright -
    the pages end up living only in the target now."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    _upload(client, job.id, freight.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()

    merge_resp = client.post(f"/api/v1/jobs/{job.id}/documents/{target.id}/merge-with/{source.id}")
    assert merge_resp.status_code == 200, merge_resp.text
    remove_resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{source.id}/file")
    assert remove_resp.status_code == 200, remove_resp.text

    db_session.expire_all()
    emptied = db_session.query(JobDocument).filter(JobDocument.id == source.id).one()
    assert emptied.file_path is None
    merged_target = db_session.query(JobDocument).filter(JobDocument.id == target.id).one()
    assert merged_target.page_count == 2


def test_refuses_merging_a_document_into_itself(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{source.id}/merge-with/{source.id}")
    assert resp.status_code == 422


def test_merging_into_an_image_target_promotes_it_to_a_pdf(client, db_session):
    """This used to be refused with a 422 - "a single image is inherently one page", treated as
    deliberately out of scope. That limitation was lifted, because it bit precisely where it
    hurt most: every document assigned from the Unclassified pile IS an image (the page image
    becomes the file), so the documents an operator creates by dragging were exactly the ones
    nothing could ever be added to - two pages of the same arrival notice became two separate
    single-page documents with no way to join them.

    The target is converted to a one-page PDF first, in place, using the same conversion the
    INSERT side of _append_to_pdf has always performed. See also
    test_deleted_pages_go_to_unclassified.py, which covers the drag-and-drop route into this."""
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    _upload_png(client, job.id, freight.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()
    pages_before = target.page_count

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{target.id}/merge-with/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    target = db_session.query(JobDocument).filter(
        JobDocument.template_document_id == freight.id).one()
    assert target.file_path.endswith(".pdf")
    assert target.page_count > pages_before


def test_refuses_merge_when_target_has_no_file_yet(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    empty_target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{empty_target.id}/merge-with/{source.id}")
    assert resp.status_code == 404


def test_refuses_merge_while_the_job_is_mid_extraction(client, db_session):
    tenant = make_tenant(db_session)
    group, bl, freight = _make_template_two_slots(db_session, tenant)
    job = _make_job_with_slots(db_session, tenant, group, bl, freight)
    _login_operator(client, db_session, tenant)

    _upload(client, job.id, bl.id)
    _upload(client, job.id, freight.id)
    source = db_session.query(JobDocument).filter(JobDocument.template_document_id == bl.id).one()
    target = db_session.query(JobDocument).filter(JobDocument.template_document_id == freight.id).one()

    job.status = "extracting"
    db_session.commit()

    resp = client.post(f"/api/v1/jobs/{job.id}/documents/{target.id}/merge-with/{source.id}")
    assert resp.status_code == 409

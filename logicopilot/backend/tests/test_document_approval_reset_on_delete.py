"""_remove_document_file (deleting a document's file - the wrong invoice, uploaded by
mistake) used to clear everything about the slot EXCEPT its approval flags. _slot_for_new_file
then reuses that exact now-empty row for whatever gets uploaded next (by design - a slot holds
as many files as the shipment has, and refilling the empty one first is how that stays true
without creating duplicate rows). Together, that meant: approve a document, delete it because
it was the wrong file, upload the correct one into the same slot - and the new, completely
different, never-reviewed document read "already approved" to the next person who opened it.
A document that never got a genuine human look could reach the ERP unreviewed.
"""
from app.api.v1.jobs import _remove_document_file
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job_with_one_approved_document(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REUSE", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/wrong-invoice.pdf", page_count=1,
                     approved=True, gk2_approved=True)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    return job, jd


def test_deleting_the_only_file_clears_its_approval_too(db_session):
    job, jd = _make_job_with_one_approved_document(db_session)
    assert jd.approved is True
    assert jd.gk2_approved is True

    _remove_document_file(db_session, job, jd)
    db_session.commit()
    db_session.refresh(jd)

    assert jd.file_path is None
    assert jd.approved is False
    assert jd.gk2_approved is False


def test_the_reused_slot_is_not_pre_approved_for_whatever_is_uploaded_next(db_session):
    """The exact live failure: _slot_for_new_file hands the next upload this SAME row back -
    it must come back unapproved, or the new file inherits the old one's review."""
    from app.api.v1.jobs import _slot_for_new_file

    job, jd = _make_job_with_one_approved_document(db_session)
    _remove_document_file(db_session, job, jd)
    db_session.commit()

    reused = _slot_for_new_file(db_session, job, jd.template_document_id)
    assert reused.id == jd.id  # confirms this really is the reuse path, not a fresh row
    assert reused.approved is False
    assert reused.gk2_approved is False

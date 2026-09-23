"""A whole-job document (set_index None) with more than one FILE - three separate weight
lists, none carrying an invoice number to be tied to one set - has each file's own row
numbering restart at row 1. Collapsing every file's rows into one bucket keyed on row number
kept only whichever file's row 1 the database happened to return first and silently dropped
the rest - the row-level version of the wide_vals bug already fixed for scalar values.
_best_rows_compare tries every file pairing instead."""
from app.api.v1.jobs import _best_rows_compare, _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


# ---- _best_rows_compare, in isolation -----------------------------------------------------

def test_finds_a_match_in_a_later_file_not_just_the_first():
    status, sv, tv = _best_rows_compare([["600"]], [["5"]])
    assert status != "match"  # sanity: these genuinely don't agree
    status, sv, tv = _best_rows_compare([["600"], ["5"]], [["5"]])
    assert status == "match"


def test_no_candidates_on_either_side_is_missing():
    assert _best_rows_compare([], [])[0] == "missing"


# ---- end to end -----------------------------------------------------------------------

def test_multiple_files_in_one_slot_each_get_tried_not_just_the_first(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    bl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading",
                              doc_type="Bill of Lading")
    wl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Weight list",
                              doc_type="Weight list")
    db_session.add_all([bl_doc, wl_doc])
    db_session.commit()
    db_session.refresh(bl_doc)
    db_session.refresh(wl_doc)

    bl_mark = FieldMark(tenant_id=tenant.id, document_id=bl_doc.id, label_name="No of pieces",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    wl_mark = FieldMark(tenant_id=tenant.id, document_id=wl_doc.id, label_name="No of pieces",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add_all([bl_mark, wl_mark])
    db_session.commit()
    db_session.refresh(bl_mark)
    db_session.refresh(wl_mark)

    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=bl_mark.id, target_mark_id=wl_mark.id))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-WIDEROWS", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # Three separate Weight list FILES - each one's own row numbering restarts at row 1.
    file_a = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=wl_doc.id,
                        page_count=1, file_index=0)
    file_b = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=wl_doc.id,
                        page_count=1, file_index=1)
    db_session.add_all([file_a, file_b])
    db_session.commit()
    db_session.refresh(file_a)
    db_session.refresh(file_b)

    # BL states 5 pieces for the whole shipment.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=bl_mark.id,
                                 label_name="No of pieces", extracted_value="5",
                                 set_index=None, row_index=1))
    # File A's row 1 says 600 (a different, unrelated line) - written to the DB first.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=wl_mark.id,
                                 job_document_id=file_a.id, label_name="No of pieces",
                                 extracted_value="600", set_index=None, row_index=1))
    # File B's row 1 is the one that actually matches the BL - must not be discarded just
    # because file A's row 1 was seen first.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=wl_mark.id,
                                 job_document_id=file_b.id, label_name="No of pieces",
                                 extracted_value="5", set_index=None, row_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"

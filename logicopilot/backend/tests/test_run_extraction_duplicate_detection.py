"""End-to-end: run_extraction itself (not just the two helpers it calls) lands a job on
"possible_duplicate" when its extracted data matches an already-settled job's, and on
"extracted" normally otherwise. OCR (get_page_ocr) and the AI field call
(extract_document_fields) are mocked - real network/file I/O has no place in a unit test -
but everything else (JobDocument/FieldMark setup, the aggregation into extracted_keyouted_data,
the checksum, the duplicate lookup, the status write) runs for real, against a real DB."""

from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_template(db_session, tenant, name="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name=name, status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    return group, tdoc


def _make_job_with_document(db_session, tenant, group, tdoc, reference):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job


def _run_with_mocked_ai(db_session, job, invoice_no: str):
    with patch("app.api.v1.jobs.get_page_ocr",
               return_value={"layout_text": f"Invoice No: {invoice_no}", "text": f"Invoice No: {invoice_no}"}), \
         patch("app.api.v1.jobs.extract_document_fields",
               return_value={"Invoice No": invoice_no}):
        run_extraction(db_session, job)
    db_session.refresh(job)


def test_a_freshly_extracted_job_with_no_match_lands_on_extracted(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-FIRST")

    _run_with_mocked_ai(db_session, job, "INV-001")

    assert job.status == "extracted"
    assert job.content_checksum is not None
    assert job.duplicate_of_job_id is None
    assert job.extracted_keyouted_data["Invoice"]["Invoice No"] == "INV-001"


def test_a_second_job_extracting_to_the_same_data_is_flagged_a_possible_duplicate(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    original = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-ORIGINAL")
    _run_with_mocked_ai(db_session, original, "INV-001")
    assert original.status == "extracted"

    resend = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-RESEND")
    _run_with_mocked_ai(db_session, resend, "INV-001")

    assert resend.status == "possible_duplicate"
    assert resend.duplicate_of_job_id == original.id
    # The original itself is untouched - only the NEW job gets flagged.
    assert original.status == "extracted"
    assert original.duplicate_of_job_id is None


def test_a_second_job_extracting_to_different_data_is_not_flagged(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)

    original = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-ORIGINAL2")
    _run_with_mocked_ai(db_session, original, "INV-001")

    different = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-DIFFERENT")
    _run_with_mocked_ai(db_session, different, "INV-002")

    assert different.status == "extracted"
    assert different.duplicate_of_job_id is None


def test_re_extracting_the_original_job_itself_does_not_flag_it_against_its_own_prior_run(db_session):
    # Re-running extraction on the SAME job (the Extract button, pressed twice) must not
    # trip the duplicate check against its own earlier checksum.
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-RERUN")

    _run_with_mocked_ai(db_session, job, "INV-001")
    assert job.status == "extracted"

    job.status = "extracting"
    db_session.commit()
    _run_with_mocked_ai(db_session, job, "INV-001")

    assert job.status == "extracted"
    assert job.duplicate_of_job_id is None

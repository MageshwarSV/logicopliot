"""Cargo Product Change Request List, item 2 ("Document Validation"): if a document is
missing one of its own ask_operator_required fields, it must not be accepted into its slot -
it is bounced to Unclassified Pages instead (same table/UI an unplaceable smart-upload page
already uses), leaving the slot empty so nothing downstream treats it as a finished document.

Confirmed scope with the user: only FieldMark.ask_operator_required fields on that document's
OWN template trigger this (not CustomField-level required fields), and only on a document's
very FIRST pass through extraction - reassigning the same (or a replacement) file to the same
slot afterwards is let through even if the field is still blank, so the operator always has a
way to accept the document and fill the field in by hand rather than being stuck in a loop.

OCR (get_page_ocr) and the AI field call (extract_document_fields) are mocked; everything else
(JobDocument/FieldMark setup, the bounce itself, Unclassified Pages rows, the "let it through
the second time" re-pass) runs for real against a real DB and a real (tiny, fake) page image
file on disk, since the bounce path checks the rendered page actually exists."""

from unittest.mock import patch

from app.api.v1.jobs import _job_doc_dir, run_extraction
from app.core.config import get_settings
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_template(db_session, tenant, *, invoice_no_required: bool):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05,
                     ask_operator_required=invoice_no_required)
    db_session.add(mark)
    db_session.commit()
    return group, tdoc, mark


def _make_job_with_document(db_session, tenant, group, tdoc, reference, *, page_count=1):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/invoice.pdf", page_count=page_count,
                     original_name="invoice.pdf")
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)

    # The bounce path checks the rendered page image actually exists on disk - same real
    # uploads_dir run_extraction itself reads from (_job_doc_dir), not a mock.
    pages_dir = _job_doc_dir(jd.id) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for p in range(1, page_count + 1):
        (pages_dir / f"page_{p}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    return job, jd


def _run_with_mocked_ai(db_session, job, invoice_no: str | None):
    text = f"Invoice No: {invoice_no}" if invoice_no else "no invoice number anywhere on this page"
    with patch("app.api.v1.jobs.get_page_ocr",
               return_value={"layout_text": text, "text": text}), \
         patch("app.api.v1.jobs.extract_document_fields",
               return_value={"Invoice No": invoice_no or ""}):
        run_extraction(db_session, job)
    db_session.refresh(job)


def test_a_blank_required_field_bounces_the_document_on_its_first_pass(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant, invoice_no_required=True)
    job, jd = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-BOUNCE1")

    _run_with_mocked_ai(db_session, job, invoice_no=None)
    db_session.refresh(jd)

    assert job.status == "extracted"  # the JOB still finishes, just without this document
    assert jd.file_path is None
    assert jd.page_count == 0
    assert (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id)
        .first()
    ) is None

    unmatched = (
        db_session.query(UnmatchedUploadPage)
        .filter(UnmatchedUploadPage.job_id == job.id)
        .all()
    )
    assert len(unmatched) == 1
    assert unmatched[0].original_filename == "invoice.pdf"
    assert unmatched[0].page_number == 1


def test_an_optional_field_left_blank_does_not_bounce_the_document(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant, invoice_no_required=False)
    job, jd = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-NOBOUNCE")

    _run_with_mocked_ai(db_session, job, invoice_no=None)
    db_session.refresh(jd)

    assert jd.file_path == "fake/invoice.pdf"
    fv = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id)
        .first()
    )
    assert fv is not None
    assert fv.extracted_value == ""
    assert db_session.query(UnmatchedUploadPage).filter(
        UnmatchedUploadPage.job_id == job.id).count() == 0


def test_a_present_required_field_does_not_bounce_the_document(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant, invoice_no_required=True)
    job, jd = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-FINE")

    _run_with_mocked_ai(db_session, job, invoice_no="INV-001")
    db_session.refresh(jd)

    assert jd.file_path == "fake/invoice.pdf"
    fv = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id)
        .first()
    )
    assert fv is not None
    assert fv.extracted_value == "INV-001"


def test_reassigning_after_a_bounce_is_let_through_even_if_still_blank(db_session):
    # Confirmed with the user: bouncing happens only on a slot's very first extraction pass -
    # once it has been through one pass (bounced or not), a later pass must never bounce it
    # again, or an operator with a genuinely blank field could never get the document accepted.
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant, invoice_no_required=True)
    job, jd = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-BOUNCE2")

    _run_with_mocked_ai(db_session, job, invoice_no=None)
    db_session.refresh(jd)
    assert jd.file_path is None  # bounced on the first pass, as above

    # The operator reassigns the same rendered page back onto the now-empty slot, exactly as
    # assign_unclassified_page does (reuses the SAME JobDocument row - _slot_for_new_file picks
    # up any slot with no file_path - which is exactly what makes this "second pass" check work).
    # The real endpoint deletes the UnmatchedUploadPage row on assignment - same here.
    db_session.query(UnmatchedUploadPage).filter(UnmatchedUploadPage.job_id == job.id).delete()
    jd.file_path = "fake/invoice.pdf"
    jd.page_count = 1
    db_session.commit()

    job.status = "extracting"
    db_session.commit()
    _run_with_mocked_ai(db_session, job, invoice_no=None)
    db_session.refresh(jd)

    assert jd.file_path == "fake/invoice.pdf"  # NOT bounced again
    fv = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id)
        .first()
    )
    assert fv is not None
    assert fv.extracted_value == ""
    # No second Unclassified Pages row was added on this pass.
    assert db_session.query(UnmatchedUploadPage).filter(
        UnmatchedUploadPage.job_id == job.id).count() == 0

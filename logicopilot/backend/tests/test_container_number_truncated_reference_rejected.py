"""A second, distinct container_number failure mode from the seal-number one (see
test_container_number_format_validation.py): a value that IS genuinely 4 letters + 7 digits
can still be wrong - found live on a 3-page OOCL waybill, where a continuation page's own
blank "CNTR. NOS." section sits right under "SEA WAYBILL NO.: OOLU2172231880" (14 chars, not
a container number). The model truncated it down to "OOLU2172231" - dropping the trailing
"880" - because that 11-character prefix happens to fit the container number shape exactly.
The ISO-6346 format check alone can't catch this; the fragment passes it perfectly. The fix:
reject a candidate if the document's own OCR text never shows it as a complete, standalone
token - every occurrence is immediately followed by another digit."""
from unittest.mock import patch

from app.api.v1.jobs import (
    _is_truncated_reference_number,
    _job_doc_dir,
    run_extraction,
)
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_a_value_immediately_followed_by_more_digits_everywhere_is_rejected():
    text = "VESSEL: X\nSEA WAYBILL NO.: OOLU2172231880\nCNTR. NOS. W/SEAL NOS.\n"
    assert _is_truncated_reference_number("OOLU2172231", text)


def test_a_value_with_at_least_one_clean_standalone_occurrence_is_trusted():
    text = "CNTR. NOS. W/SEAL NOS.\nGAOU7433235\n/OOLHAV4231\n8 PALLETS"
    assert not _is_truncated_reference_number("GAOU7433235", text)


def test_a_value_not_present_in_the_text_at_all_is_not_flagged_here():
    # Not this check's job - _is_valid_container_number and plain absence handle that.
    assert not _is_truncated_reference_number("WHSU1234567", "nothing relevant here")


def test_blank_text_or_value_is_never_flagged():
    assert not _is_truncated_reference_number("GAOU7433235", "")
    assert not _is_truncated_reference_number("", "SEA WAYBILL NO.: OOLU2172231880")


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [r.extracted_value for r in rows]


def test_the_exact_live_shape_end_to_end(db_session):
    """One real container printed cleanly, plus a continuation page whose blank CNTR section
    sits under a SEA WAYBILL NO. the model mis-read as a second container."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(bl)
    db_session.commit()
    db_session.refresh(bl)
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="container_number",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                             is_multi_value=True, standalone_multi_value=True))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-1E90C7LIKE", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                     file_path="fake/does-not-exist.pdf", page_count=2)
    db_session.add(jd)
    db_session.commit()

    page_text = (
        "=== PAGE 1 ===\nCNTR. NOS. W/SEAL NOS.\nGAOU7433235\n/OOLHAV4231\n8 PALLETS\n\n"
        "=== PAGE 2 ===\nSEA WAYBILL NO.: OOLU2172231880\nCNTR. NOS. W/SEAL NOS.\n"
        "(blank - continuation page)\n"
    )
    text_rows = [{"container_number": "GAOU7433235"}, {"container_number": "OOLU2172231"}]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": page_text, "text": page_text, "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "container_number") == ["GAOU7433235"]

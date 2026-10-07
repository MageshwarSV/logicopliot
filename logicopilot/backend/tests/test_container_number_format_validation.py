"""A prompt instruction alone ("a 3-letter code is a seal number, not a container number")
isn't a reliable enough guarantee - JOB-7372E8 extracted 5 real containers (WHSU...)
correctly interleaved with 5 of their own seal numbers (WHA...), one row each, because the
model still read each seal number's own row as if it were a second container. This is the
deterministic backstop: whatever a container_number mark's extraction returns, a row that
isn't genuinely 4 letters + 7 digits (ISO 6346) is dropped outright, never trusted."""
from unittest.mock import patch

from app.api.v1.jobs import _is_valid_container_number, _job_doc_dir, run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_valid_iso6346_formats_pass():
    assert _is_valid_container_number("WHSU5099778")
    assert _is_valid_container_number("whsu5099778")  # case-insensitive
    assert _is_valid_container_number(" WHSU5099778 ")  # tolerant of whitespace


def test_a_3_letter_seal_number_is_rejected():
    assert not _is_valid_container_number("WHA3170485")


def test_a_purely_numeric_string_is_rejected():
    assert not _is_valid_container_number("1234567890")


def test_wrong_digit_count_is_rejected():
    assert not _is_valid_container_number("WHSU123")  # too few digits
    assert not _is_valid_container_number("WHSU12345678")  # too many digits


def test_blank_or_none_is_rejected():
    assert not _is_valid_container_number(None)
    assert not _is_valid_container_number("")


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [r.extracted_value for r in rows]


def test_seal_numbers_interleaved_with_real_containers_are_dropped_not_stored(db_session):
    """The exact live shape of JOB-7372E8: 5 real containers, each immediately followed in
    the read by its own seal number treated as if it were a second container row."""
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

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-7372E8LIKE", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=bl.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()

    text_rows = [{"container_number": v} for v in (
        "WHSU5099778", "WHA3170485", "WHSU5770968", "WHA3170573", "WHSU6752383",
        "WHA3170470", "WHSU6789947", "WHA3170599", "WHSU8125443", "WHA3168473",
    )]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some bl text", "text": "some bl text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "container_number") == [
        "WHSU5099778", "WHSU5770968", "WHSU6752383", "WHSU6789947", "WHSU8125443",
    ]

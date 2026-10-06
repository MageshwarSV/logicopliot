"""The text-vs-image row-count cross-check in run_extraction defaults to "whichever read found
MORE rows wins", built for dense product-line tables where the demonstrated failure mode is
rows going missing or merging (see test_row_total_cross_check.py). JOB-9FCEC5 showed the
opposite failure for a standalone field: the OCR text correctly read exactly 1 real container
(WHSU8379514), but the page image hallucinated 4 more by scraping nearby invoice/reference
numbers - and "more rows wins" then discarded the one correct answer in favor of the image's 5.

A standalone field has no printed total or Document AI table geometry backing up its own row
count the way a product table does, so the whole vision cross-check (not just the row-count
preference) is skipped for a standalone group - it trusts its own text reading, exactly as if
no page image existed at all. Row-aligned (product-line) marks must be completely unaffected."""
from unittest.mock import patch

from app.api.v1.jobs import _job_doc_dir, run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [r.extracted_value for r in rows]


def _make_job_with_page(db_session, tenant, group, tdoc, reference):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    pages_dir = _job_doc_dir(jd.id) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "page_1.png").write_bytes(_TINY_PNG)
    return job


def test_standalone_field_keeps_the_text_read_even_when_vision_finds_more_rows(db_session):
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
    job = _make_job_with_page(db_session, tenant, group, bl, "JOB-9FCEC5LIKE")

    text_rows = [{"container_number": "WHSU8379514"}]
    vision_rows = [{"container_number": v} for v in (
        "WHSU8379514", "REGU5094361", "SHA2602854", "1234567890", "ABCD1234567",
    )]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some bl text", "text": "some bl text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows) as fake_vision:
        run_extraction(db_session, job)
        # The vision cross-check is skipped entirely for a standalone group - it must never
        # even be called, not just overridden afterwards.
        fake_vision.assert_not_called()

    assert _row_values(db_session, job, "container_number") == ["WHSU8379514"]


def test_row_aligned_marks_on_the_same_document_still_prefer_vision_when_it_finds_more_rows(db_session):
    """The scoped fix must not change behavior for the ordinary product-line case - only the
    standalone group in the same document's mark_groups loop is affected."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 2", status="approved")
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
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="cth_code",
                             page_number=1, x=0.1, y=0.2, width=0.1, height=0.1, is_multi_value=True))
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, bl, "JOB-MIXEDGROUPS")

    def fake_text(ocr_text, row_specs, detected_row_count=None):
        labels = sorted(f["label"] for f in row_specs)
        if labels == ["container_number"]:
            return [{"container_number": "WHSU8379514"}]
        if labels == ["cth_code"]:
            return [{"cth_code": "1001"}, {"cth_code": "1002"}]
        raise AssertionError(f"unexpected batch: {labels}")

    def fake_vision(image_paths, row_specs, detected_row_count=None):
        labels = sorted(f["label"] for f in row_specs)
        if labels == ["container_number"]:
            raise AssertionError("vision must never be called for a standalone group")
        if labels == ["cth_code"]:
            return [{"cth_code": "1001"}, {"cth_code": "1002"}, {"cth_code": "1003"}]
        raise AssertionError(f"unexpected batch: {labels}")

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some bl text", "text": "some bl text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=fake_text), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", side_effect=fake_vision):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "container_number") == ["WHSU8379514"]
    # The row-aligned mark, in its own separate call, keeps the existing "more rows wins"
    # behavior completely unchanged - it ends up with vision's 3, not text's 2.
    assert _row_values(db_session, job, "cth_code") == ["1001", "1002", "1003"]

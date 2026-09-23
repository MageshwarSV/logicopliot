"""run_extraction populates JobFieldValue.found_page/x/y/width/height by locating the
extracted value's own text within the REAL document's OCR word boxes - never from
FieldMark's static template-drawn position."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    # A deliberately WRONG template position (top-left corner) - proves the highlight comes
    # from the real OCR match below, not from this static box.
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Port of delivery",
                     page_number=1, x=0.01, y=0.01, width=0.05, height=0.02)
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


def _field_value(db_session, job, label):
    return (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .first()
    )


def test_found_position_comes_from_the_real_document_not_the_template_mark(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-FOUNDPOS1")

    tokens = [
        {"text": "Port", "x0": 0.08, "y0": 0.35, "x1": 0.13, "y1": 0.37},
        {"text": "of", "x0": 0.14, "y0": 0.35, "x1": 0.17, "y1": 0.37},
        {"text": "delivery:", "x0": 0.18, "y0": 0.35, "x1": 0.28, "y1": 0.37},
        {"text": "Chicago,", "x0": 0.29, "y0": 0.35, "x1": 0.36, "y1": 0.37},
        {"text": "USA", "x0": 0.37, "y0": 0.35, "x1": 0.40, "y1": 0.37},
    ]
    with patch(
        "app.api.v1.jobs.get_page_ocr",
        return_value={
            "layout_text": "Port of delivery: Chicago, USA",
            "text": "Port of delivery: Chicago, USA",
            "tokens": tokens,
        },
    ), patch(
        "app.api.v1.jobs.extract_document_fields",
        return_value={"Port of delivery": "Chicago, USA"},
    ):
        run_extraction(db_session, job)

    fv = _field_value(db_session, job, "Port of delivery")
    assert fv is not None
    assert fv.found_page == 1
    assert round(fv.found_x, 2) == 0.29
    assert round(fv.found_y, 2) == 0.35
    # Confirms this is NOT the template mark's static box (x=0.01, y=0.01).
    assert fv.found_x != 0.01
    assert fv.found_y != 0.01


def test_no_confident_match_leaves_found_position_null(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-FOUNDPOS2")

    tokens = [{"text": "Nothing", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.12}]
    with patch(
        "app.api.v1.jobs.get_page_ocr",
        return_value={"layout_text": "Nothing", "text": "Nothing", "tokens": tokens},
    ), patch(
        "app.api.v1.jobs.extract_document_fields",
        # The AI reformatted the value so much it no longer appears verbatim in the OCR text.
        return_value={"Port of delivery": "Chicago, United States of America"},
    ):
        run_extraction(db_session, job)

    fv = _field_value(db_session, job, "Port of delivery")
    assert fv is not None
    assert fv.extracted_value == "Chicago, United States of America"
    assert fv.found_page is None
    assert fv.found_x is None


def test_vision_fallback_with_no_ocr_tokens_leaves_found_position_null(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-FOUNDPOS3")

    # Empty OCR text triggers the vision-fallback path - no tokens are ever available there.
    with patch(
        "app.api.v1.jobs.get_page_ocr",
        return_value={"layout_text": "", "text": "", "tokens": []},
    ), patch(
        "app.api.v1.jobs.extract_document_fields_from_images",
        return_value={"Port of delivery": "Chicago, USA"},
    ):
        run_extraction(db_session, job)

    fv = _field_value(db_session, job, "Port of delivery")
    assert fv is not None
    assert fv.extracted_value == "Chicago, USA"
    assert fv.found_page is None

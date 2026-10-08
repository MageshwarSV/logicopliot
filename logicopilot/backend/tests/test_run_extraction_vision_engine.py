"""run_extraction's extraction_engine wiring - the default ("ocr_gpt4o_mini") must behave
exactly as before this setting existed (OCR text used whenever present), and
"gpt5_mini_vision" must force every document through the image readers with
settings.vision_engine_model, regardless of whether OCR text is also available - unlike the
pre-existing "OCR genuinely found nothing" fallback, which only ever uses the default model."""
from unittest.mock import patch

from app.core.system_settings import set_extraction_engine
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.api.v1.jobs import _job_doc_dir, run_extraction
from tests.conftest import make_tenant

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)


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


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [r.extracted_value for r in rows]


def test_default_engine_uses_text_reader_when_ocr_text_is_present(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="supplier_name",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-ENGINE-DEFAULT")

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some real text", "text": "some real text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields",
               return_value={"supplier_name": "ACME CORP"}) as mock_text, \
         patch("app.api.v1.jobs.extract_document_fields_from_images") as mock_vision:
        run_extraction(db_session, job)

    mock_text.assert_called_once()
    mock_vision.assert_not_called()
    assert mock_text.call_args.args[0] == "some real text"


def test_vision_engine_forces_image_reader_even_with_ocr_text_present(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 2", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="supplier_name",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-ENGINE-VISION")
    set_extraction_engine(db_session, "gpt5_mini_vision")

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some real text", "text": "some real text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields") as mock_text, \
         patch("app.api.v1.jobs.extract_document_fields_from_images",
               return_value={"supplier_name": "ACME CORP"}) as mock_vision:
        run_extraction(db_session, job)

    mock_text.assert_not_called()
    mock_vision.assert_called_once()
    assert mock_vision.call_args.kwargs["model"] == "gpt-5-mini"
    assert _row_values(db_session, job, "supplier_name") == ["ACME CORP"]


def test_vision_engine_still_rejects_a_seal_number_for_container_number(db_session):
    """The container-number validators must keep applying under the vision engine too - they
    run unconditionally after rows are finalized, independent of which engine produced them."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 3", status="approved")
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
    job = _make_job_with_page(db_session, tenant, group, bl, "JOB-ENGINE-VISION-CONTAINER")
    set_extraction_engine(db_session, "gpt5_mini_vision")

    vision_rows = [{"container_number": "WHSU5099778"}, {"container_number": "WHA3170485"}]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some bl text", "text": "some bl text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows_from_images",
               return_value=vision_rows) as mock_vision_rows:
        run_extraction(db_session, job)

    assert mock_vision_rows.call_args.kwargs["model"] == "gpt-5-mini"
    assert _row_values(db_session, job, "container_number") == ["WHSU5099778"]

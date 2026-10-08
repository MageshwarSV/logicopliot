"""recompute_mark's extraction_engine wiring - it backfills a mark "same as a fresh
extraction would" (its own docstring), so it must respect the same engine setting
run_extraction does, even though it has its own independent used_vision/model logic."""
from unittest.mock import patch

from app.core.system_settings import set_extraction_engine
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="approved", mode="Sea Export")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="EXPORTER",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return group, tdoc, mark


def _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-MARKBACKFILL-VISION"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted",
             validation_approved=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    return job, jd


def test_vision_engine_routes_recompute_through_the_image_reader_with_the_configured_model(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job, jd = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 template_document_id=tdoc.id, job_document_id=jd.id,
                                 label_name="EXPORTER", extracted_value="4S Logistics"))
    db_session.commit()
    set_extraction_engine(db_session, "gpt5_mini_vision")

    make_user(db_session, role=SUPER_ADMIN, email="sa-vision1@example.com")
    login(client, "sa-vision1@example.com")

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "Exporter:- Real Exporter Co", "text": "Exporter:- Real Exporter Co",
                            "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields") as mock_text, \
         patch("app.api.v1.jobs.extract_document_fields_from_images",
               return_value={"EXPORTER": "Real Exporter Co"}) as mock_vision:
        resp = client.post(f"/api/v1/jobs/{job.id}/marks/{mark.id}/recompute")

    assert resp.status_code == 200, resp.text
    mock_text.assert_not_called()
    mock_vision.assert_called_once()
    assert mock_vision.call_args.kwargs["model"] == "gpt-5-mini"
    assert resp.json()[0]["extracted_value"] == "Real Exporter Co"

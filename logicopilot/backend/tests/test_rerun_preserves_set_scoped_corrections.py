"""A per-row CUSTOM field (job_document_id is always null for these - see run_extraction's
per_row write loop) can have "row 1" on invoice set 1 and "row 1" on invoice set 2 as two
genuinely different products, sharing row_index but never set_index. The correction-preserving
fix in run_extraction (see test_missing_mark_values_bug.py) must key its snapshot on
set_index too, not just row_index, or a job with two invoices - exactly this shape, found live
on JOB-37DF67 - would have one set's correction silently overwrite the other's on a re-run."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

INV1_TEXT = "Invoice No: INV-001\nWidget A"
INV2_TEXT = "Invoice No: INV-002\nWidget B"


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    invoice_no_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                               page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    line_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_description",
                          page_number=1, x=0.1, y=0.2, width=0.2, height=0.05, is_multi_value=True)
    db_session.add_all([invoice_no_mark, line_mark])
    db_session.commit()

    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="RITC No.",
                     kind="hardcoded", hardcoded_value="FIXED-CODE", per_row=True)
    db_session.add(cf)
    db_session.commit()
    return group, tdoc


def _make_job_with_two_invoices(db_session, tenant, group, tdoc):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TWOSETS", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    jd1 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                      file_path="fake/inv1.pdf", page_count=1, file_index=0)
    jd2 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                      file_path="fake/inv2.pdf", page_count=1, file_index=1)
    db_session.add_all([jd1, jd2])
    db_session.commit()
    db_session.refresh(jd1)
    db_session.refresh(jd2)
    return job, jd1, jd2


def _run(db_session, job, jd1, jd2):
    def _ocr_for(job_doc_dir, page):
        text = INV1_TEXT if str(jd1.id) in str(job_doc_dir) else INV2_TEXT
        return {"layout_text": text, "text": text, "tokens": []}

    def _fields_for(ocr_text, fields, image_paths=None):
        return {"Invoice No": "INV-001" if "INV-001" in ocr_text else "INV-002"}

    def _rows_for(ocr_text, fields, expected_row_count=None, image_paths=None):
        return [{"item_description": "Widget A" if "INV-001" in ocr_text else "Widget B"}]

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields", side_effect=_fields_for), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=_rows_for):
        run_extraction(db_session, job)


def _ritc_rows(db_session, job):
    return (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "RITC No.")
        .order_by(JobFieldValue.set_index)
        .all()
    )


def test_rerun_keeps_each_sets_own_correction_separate(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job, jd1, jd2 = _make_job_with_two_invoices(db_session, tenant, group, tdoc)

    _run(db_session, job, jd1, jd2)

    ritc_rows = _ritc_rows(db_session, job)
    assert len(ritc_rows) == 2
    assert {r.set_index for r in ritc_rows} == {1, 2}
    # Two DIFFERENT corrections on the same row_index, one per set - the exact shape found on
    # the real job (both happened to be corrected to the same value there; this test proves
    # the general case where they differ).
    for r in ritc_rows:
        r.corrected_value = "SET-1-CODE" if r.set_index == 1 else "SET-2-CODE"
    db_session.commit()

    _run(db_session, job, jd1, jd2)

    ritc_rows_after = _ritc_rows(db_session, job)
    assert len(ritc_rows_after) == 2
    by_set = {r.set_index: r.corrected_value for r in ritc_rows_after}
    assert by_set == {1: "SET-1-CODE", 2: "SET-2-CODE"}

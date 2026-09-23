"""CustomField.multi_value_from_document — the AI-computed counterpart to a Mark ticked
"multiple values in this document": instead of one value, the field reads its own document(s)
directly and gets one JobFieldValue per row found, via compute_custom_field_rows."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
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


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [(r.row_index, r.extracted_value) for r in rows]


def _empty_ocr_patches():
    return patch("app.api.v1.jobs.get_page_ocr", return_value={"layout_text": "", "text": "", "tokens": []}), \
        patch("app.api.v1.jobs.extract_document_fields", return_value={})


def test_multi_value_writes_one_row_per_returned_value(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Container Number",
                     kind="ai", ai_prompt="Every container number on the bill of lading",
                     multi_value_from_document=True)
    db_session.add(cf)
    db_session.commit()
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-MULTIROW1")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch(
        "app.core.llm.compute_custom_field_rows",
        return_value=["CONU1111111", "TCLU2222222", "MSCU3333333"],
    ):
        run_extraction(db_session, job)

    rows = _row_values(db_session, job, "Container Number")
    assert rows == [(1, "CONU1111111"), (2, "TCLU2222222"), (3, "MSCU3333333")]


def test_multi_value_with_no_matches_writes_no_rows(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Container Number",
                     kind="ai", ai_prompt="Every container number", multi_value_from_document=True)
    db_session.add(cf)
    db_session.commit()
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-MULTIROW2")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch("app.core.llm.compute_custom_field_rows", return_value=[]):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "Container Number") == []


def test_ai_field_without_the_flag_still_writes_a_single_value(db_session):
    """Backward compatibility: an existing AI-computed field with multi_value_from_document
    left at its default (False) must keep behaving exactly as before — one value, no
    row_index — not silently switch to the rows path."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Total Weight",
                     kind="ai", ai_prompt="Total net weight in KGS")
    db_session.add(cf)
    db_session.commit()
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-MULTIROW3")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch("app.core.llm.compute_custom_field", return_value="1234.5"):
        run_extraction(db_session, job)

    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "Total Weight")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extracted_value == "1234.5"
    assert rows[0].row_index is None


def test_multi_value_ignored_for_hardcoded_kind(db_session):
    """multi_value_from_document only means something for kind="ai" — a hardcoded field that
    somehow has it set must still just use its fixed value, never call the rows path."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Shipping Line",
                     kind="hardcoded", hardcoded_value="KSS ROADWAYS", multi_value_from_document=True)
    db_session.add(cf)
    db_session.commit()
    job = _make_job_with_document(db_session, tenant, group, tdoc, "JOB-MULTIROW4")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch("app.core.llm.compute_custom_field_rows") as mock_rows:
        run_extraction(db_session, job)
        mock_rows.assert_not_called()

    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "Shipping Line")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].extracted_value == "KSS ROADWAYS"

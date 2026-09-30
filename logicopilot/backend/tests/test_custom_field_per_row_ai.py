"""CustomField.kind == "ai" with per_row == True — a value read from a document for a
job's OWN already-established product lines, via compute_custom_field_per_row. Before this,
a per-row field's kind was only ever honoured as "lookup" (a reference sheet) or implicitly
"hardcoded" (run_extraction) / always "hardcoded" regardless of kind (recompute_custom_field) -
an "ai" per-row field silently wrote nothing but the field's own (usually empty)
hardcoded_value. Both call sites are covered here.

The central property under test: a blank answer for one line must stay THAT line's blank
answer, never compact the list and shift every later line's real answer up by one row - see
compute_custom_field_per_row's own docstring for why that matters specifically for a
customs-classification-style field."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    # A real multi-value MARK is what actually establishes this job's product lines during
    # run_extraction (line_keys is derived from row-indexed JobFieldValues, and those only
    # exist once something has written them - here, the row-extraction path a multi-value
    # mark triggers, mocked below via extract_document_rows).
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="product_description",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc, reference, status="extracting"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    return job, jd


def _make_job_with_lines(db_session, tenant, group, tdoc, reference, n_lines=3, status="extracting"):
    """For tests that bypass run_extraction (it wipes all JobFieldValues at the start of
    every run) and instead seed the job's product lines directly - recompute_custom_field
    reads whatever row-indexed values already exist, nothing more."""
    job, jd = _make_job(db_session, tenant, group, tdoc, reference, status=status)
    for i in range(1, n_lines + 1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id,
            label_name="product_description", extracted_value=f"WIDGET {i}", row_index=i,
        ))
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
    # Non-empty OCR text: empty text makes run_extraction fall back to a vision-based row
    # read (0 page images on disk here, since these tests never write one), bypassing the
    # extract_document_rows mock entirely.
    return patch("app.api.v1.jobs.get_page_ocr",
                return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
        patch("app.api.v1.jobs.extract_document_fields", return_value={})


def test_run_extraction_writes_one_row_per_existing_line_preserving_blanks(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                     kind="ai", per_row=True,
                     ai_prompt="The CTH code printed for this exact product line, if any.")
    db_session.add(cf)
    db_session.commit()
    job, _jd = _make_job(db_session, tenant, group, tdoc, "JOB-PERROWAI1")

    text_rows = [{"product_description": f"WIDGET {i}"} for i in (1, 2, 3)]
    p1, p2 = _empty_ocr_patches()
    # Line 2 has nothing printed - its blank must stay line 2's answer, not collapse the
    # list down to two entries and shift line 3's real answer onto line 2.
    with p1, p2, \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch(
             "app.core.llm.compute_custom_field_per_row",
             return_value=["8483109090", "", "8483109091"],
         ):
        run_extraction(db_session, job)

    assert _row_values(db_session, job, "Document CTH") == [
        (1, "8483109090"), (2, ""), (3, "8483109091"),
    ]


def test_run_extraction_per_row_ai_with_no_line_items_writes_nothing(db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                     kind="ai", per_row=True, ai_prompt="The CTH code for this line, if any.")
    db_session.add(cf)
    db_session.commit()
    job, _jd = _make_job(db_session, tenant, group, tdoc, "JOB-PERROWAI2")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, \
         patch("app.api.v1.jobs.extract_document_rows", return_value=[]), \
         patch("app.core.llm.compute_custom_field_per_row") as mock_per_row:
        run_extraction(db_session, job)
        mock_per_row.assert_not_called()

    assert _row_values(db_session, job, "Document CTH") == []


def test_recompute_per_row_ai_field_actually_recomputes_not_blanks(client, db_session):
    """The bug this closes: recompute_custom_field's per-row branch used to apply
    cf.hardcoded_value to every line regardless of kind, so recomputing a per-row "ai" (or
    "lookup") field silently blanked it instead of recomputing it."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                     kind="ai", per_row=True, ai_prompt="The CTH code for this line, if any.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = _make_job_with_lines(db_session, tenant, group, tdoc, "JOB-PERROWAI3", n_lines=2,
                               status="extracted")

    make_user(db_session, role=SUPER_ADMIN, email="sa-cth@example.com")
    login(client, "sa-cth@example.com")

    with patch("app.core.llm.compute_custom_field_per_row", return_value=["9018390000", ""]):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text

    assert _row_values(db_session, job, "Document CTH") == [(1, "9018390000"), (2, "")]

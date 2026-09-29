"""_numeric_total_field and its use in run_extraction's text-vs-image row cross-check.

The existing cross-check (_row_sums_disagree, see test_row_sums_disagree.py) only compares
the two candidate row-reads against EACH OTHER, and defaults to preferring whichever found
MORE rows whenever their counts disagree outright, on the assumption a drop/merge losing a
row is the only real failure mode. Found live on JOB-290770: an invoice's page-image read
invented a 4th row by mistaking a PO number for a new product (item_quantity summing to
3304), while the OCR text read's 3 rows summed to exactly 5226 - the invoice's own printed
total_quantity. "More rows wins" traded a correct read for a wrong one. Where the template
also captures a column's own printed total as a plain field alongside its row-level one,
that total settles which candidate actually adds up, ahead of row count entirely.
"""
from unittest.mock import patch

from app.api.v1.jobs import _job_doc_dir, _numeric_total_field, run_extraction
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


# ---- _numeric_total_field, in isolation -------------------------------------------------------

def test_finds_a_total_field_named_total_plus_the_bare_column():
    assert _numeric_total_field({"total_quantity": "5226"}, "item_quantity") == 5226


def test_finds_a_total_field_named_the_bare_column_plus_total():
    assert _numeric_total_field({"amount_total": "22733.10"}, "product_amount") == 22733.10


def test_none_when_no_matching_total_field_exists():
    assert _numeric_total_field({"shipper": "MEIKO ELECTRONICS"}, "item_quantity") is None


def test_none_when_the_matching_field_is_not_numeric():
    assert _numeric_total_field({"total_quantity": "see attached"}, "item_quantity") is None


# ---- end to end, through run_extraction ---------------------------------------------------------

def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    total_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="total_quantity",
                           page_number=1, x=0.1, y=0.9, width=0.1, height=0.05, is_multi_value=False)
    db_session.add_all([row_mark, total_mark])
    db_session.commit()
    return group, tdoc


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


def test_keeps_the_text_read_when_only_its_sum_matches_the_printed_total(db_session):
    """The exact live shape: text finds the correct 3 rows (299+3000+1927=5226, matching the
    invoice's own total_quantity); the image invents a 4th, unrelated row (summing to 3304).
    Row count alone would have picked the image's 4 - the printed total says the text's 3
    are right."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-290770")

    text_rows = [{"item_quantity": v} for v in ("299", "3000", "1927")]
    vision_rows = [{"item_quantity": v} for v in ("1", "299", "3000", "4")]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={"total_quantity": "5226"}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["299", "3000", "1927"]


def test_still_prefers_more_rows_when_no_printed_total_is_available(db_session):
    """Without a captured total_quantity field to check against, the row-count decision
    falls back to exactly the pre-existing "more rows" preference - this feature must never
    make a template WITHOUT a total field behave differently than before it existed."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 2", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-NOTOTAL")

    text_rows = [{"item_quantity": v} for v in ("10", "20")]
    vision_rows = [{"item_quantity": v} for v in ("10", "20", "30")]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["10", "20", "30"]


# ---- Document AI's own detected table row count, as a third cross-check signal --------------

def test_detected_row_count_settles_a_disagreement_with_no_printed_total_to_check(db_session):
    """A case the existing printed-total tie-breaker structurally cannot reach at all - no
    total_quantity field on this template - but Document AI's own table geometry detected
    exactly 2 rows, which only the text read matches (the image invented a 3rd)."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 3", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-DETECTED")

    text_rows = [{"item_quantity": v} for v in ("10", "20")]
    vision_rows = [{"item_quantity": v} for v in ("10", "20", "30")]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text",
                            "tokens": [], "table_row_counts": [2]}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["10", "20"]


def test_no_detected_table_falls_through_to_existing_behaviour_unchanged(db_session):
    """table_row_counts omitted entirely (Document AI found no ruled/structured table on this
    page) must behave exactly like before this feature existed - "more rows wins", same as
    test_still_prefers_more_rows_when_no_printed_total_is_available."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 4", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-NOTABLE")

    text_rows = [{"item_quantity": v} for v in ("10", "20")]
    vision_rows = [{"item_quantity": v} for v in ("10", "20", "30")]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["10", "20", "30"]


def test_both_reads_agreeing_on_a_wrong_count_is_logged_not_silently_kept(db_session, caplog):
    """Text and image both read the same 2 rows, but Document AI's own table detected 3 -
    nothing here can know WHICH row the agreed-upon read is missing, so this only has to be
    visible (a warning), never auto-corrected by inventing a row."""
    import logging

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 5", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    row_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(row_mark)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-BOTHWRONG")

    same_rows = [{"item_quantity": v} for v in ("10", "20")]

    with caplog.at_level(logging.WARNING, logger="app.api.v1.jobs"), \
         patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text",
                            "tokens": [], "table_row_counts": [3]}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=same_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=same_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["10", "20"]
    assert any("neither reader" in rec.message for rec in caplog.records)

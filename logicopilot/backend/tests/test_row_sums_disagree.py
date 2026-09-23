"""_row_sums_disagree: a general, layout-independent safety net for multi-value row
extraction. run_extraction already cross-checks OCR-text vs page-image reads of the same
table, but only by ROW COUNT (see _row_read_looks_broken and the caller right after it) - blind
to a read that drops one row's real value and fills the gap with something else (a
neighbouring row's value, the table's own grand total), which can still produce the RIGHT row
count by coincidence. Found live: an invoice's item_quantity read 7 rows both ways - the count
no one had reason to doubt - while row 4's real value had been dropped and the table's own
dollar TOTAL misread in its place. This closes that gap by reconciling the two reads' own
numeric column SUMS, the same arithmetic check _best_rows_to_scalar_compare already uses to
verify a job's documents against each other, applied here to verify one document's two reading
METHODS against each other before either is trusted - it never asks what the table looks like,
so it needs no per-layout tuning."""
from unittest.mock import patch

from app.api.v1.jobs import _job_doc_dir, _row_sums_disagree, run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

# A minimal valid 1x1 transparent PNG - just needs to exist on disk for image_paths to be
# non-empty; its actual pixel content is never read (extract_document_rows_from_images itself
# is mocked in the end-to-end test below).
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)


# ---- _row_sums_disagree, in isolation --------------------------------------------------------

def test_agrees_when_the_totals_match():
    rows_a = [{"qty": "16"}, {"qty": "20"}]
    rows_b = [{"qty": "16"}, {"qty": "20"}]
    assert _row_sums_disagree(rows_a, rows_b, ["qty"]) is False


def test_disagrees_when_a_middle_value_was_dropped_and_replaced():
    # The exact live shape: same row COUNT, wrong individual values.
    rows_a = [{"qty": "1960"}, {"qty": "1820"}, {"qty": "1960"}, {"qty": "1960"},
             {"qty": "1248"}, {"qty": "2688"}, {"qty": "1152"}]
    rows_b = [{"qty": "1960"}, {"qty": "1820"}, {"qty": "1960"}, {"qty": "1248"},
             {"qty": "2688"}, {"qty": "1152"}, {"qty": "20429"}]
    assert _row_sums_disagree(rows_a, rows_b, ["qty"]) is True


def test_small_rounding_difference_is_not_a_disagreement():
    rows_a = [{"qty": "500.0"}, {"qty": "500.0"}]
    rows_b = [{"qty": "999.6"}, {"qty": "0.4"}]
    assert _row_sums_disagree(rows_a, rows_b, ["qty"]) is False


def test_a_non_numeric_column_has_nothing_to_reconcile():
    rows_a = [{"desc": "Cover Front LH"}, {"desc": "Cover Rear LH"}]
    rows_b = [{"desc": "Something Else Entirely"}, {"desc": "Totally Different"}]
    assert _row_sums_disagree(rows_a, rows_b, ["desc"]) is False


def test_checks_every_label_not_just_the_first():
    rows_a = [{"qty": "10", "price": "5"}, {"qty": "20", "price": "5"}]
    rows_b = [{"qty": "10", "price": "5"}, {"qty": "20", "price": "9"}]
    assert _row_sums_disagree(rows_a, rows_b, ["qty", "price"]) is True


# ---- end to end, through run_extraction -------------------------------------------------------

def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return group, tdoc, mark


def test_vision_preferred_when_row_counts_agree_but_sums_do_not(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, mark = _make_template(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SUMCHECK", status="extracting")
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

    text_rows = [{"item_quantity": v} for v in ("1960", "1820", "1960", "1248", "2688", "1152", "20429")]
    vision_rows = [{"item_quantity": v} for v in ("1960", "1820", "1960", "1960", "1248", "2688", "1152")]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["1960", "1820", "1960", "1960", "1248", "2688", "1152"]

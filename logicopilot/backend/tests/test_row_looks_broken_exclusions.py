"""_row_read_looks_broken (run_extraction's own nested helper) used to flag ANY field that
came back identical across every row as a sign of a broken read - a header/section label
bled into every row instead of genuine per-row data. That flag disables every downstream
cross-check (_row_sums_disagree, the printed-total check in test_row_total_cross_check.py)
for BOTH candidates, on the theory a "broken" read must never be trusted regardless of what
else it says.

Found on JOB-290770: three genuine rows, one product shipped under three different PO
numbers, at the SAME unit price - so product_description, item_material_code and
item_unit_price were all correctly, legitimately identical on every row. That correct read
got flagged "broken" forever, which silently disabled the very cross-checks that would have
caught its ALSO-wrong item_quantity values, because both candidates now look "broken" and
the checks require neither to. This exercises the fix: identity/descriptive columns
(description, material/part code, unit price, quantity type) are excluded from the
broken-check, while a genuinely smeared column (a PO number identical on every row, the
ORIGINAL failure this function was built to catch) still trips it.
"""
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


def test_identical_description_and_price_do_not_block_the_total_cross_check(db_session):
    """The exact live shape: item_material_code, product_description and item_unit_price are
    legitimately identical on all 3 real rows (one product, three POs, one price). Without
    the exclusion, that would mark the text read "broken" and disable the printed-total
    check entirely, leaving the wrong item_quantity values (1, 1, 3000) uncorrected despite
    the image read having the right ones (299, 3000, 1927, matching total_quantity=5226)."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    marks = [
        FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_material_code",
                  page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True),
        FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                  page_number=1, x=0.2, y=0.1, width=0.1, height=0.1, is_multi_value=True),
        FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="total_quantity",
                  page_number=1, x=0.1, y=0.9, width=0.1, height=0.05, is_multi_value=False),
    ]
    db_session.add_all(marks)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-290770")

    # The real live shape: text correctly reads the material code identically on all 3 rows
    # but garbles the quantities; the image finally gets the quantities right but garbles
    # the tiny alphanumeric material code WORSE than text did. A wholesale swap to vision
    # would fix quantity at the cost of material_code - the merge must fix quantity (it has
    # a matching printed total) while leaving material_code exactly as text read it (no
    # total to arbitrate that column, and plain disagreement alone is not evidence text was
    # wrong there).
    text_rows = [
        {"item_material_code": "AA1S0000059AA/5250-3500", "item_quantity": v}
        for v in ("1", "1", "3000")
    ]
    vision_rows = [
        {"item_material_code": "AA15000059A/SV536-3500", "item_quantity": v}
        for v in ("299", "3000", "1927")
    ]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={"total_quantity": "5226"}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    qty_rows = (db_session.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_quantity")
                .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in qty_rows] == ["299", "3000", "1927"]

    material_rows = (db_session.query(JobFieldValue)
                     .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_material_code")
                     .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in material_rows] == ["AA1S0000059AA/5250-3500"] * 3


def test_a_genuinely_smeared_po_number_still_counts_as_broken(db_session):
    """The ORIGINAL failure this function exists to catch, still caught after the fix: a PO
    number identical on every row (a header bled into every row's own PO Number cell) while
    item_quantity correctly varies - not an identity/descriptive column, so still flagged
    broken, and the OTHER (unbroken) candidate is preferred."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 2", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    marks = [
        FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_po_no",
                  page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True),
        FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_quantity",
                  page_number=1, x=0.2, y=0.1, width=0.1, height=0.1, is_multi_value=True),
    ]
    db_session.add_all(marks)
    db_session.commit()
    job = _make_job_with_page(db_session, tenant, group, tdoc, "JOB-SMEARED")

    # Text: the section heading "VVVF" bled into every row's PO number - broken.
    text_rows = [{"item_po_no": "VVVF", "item_quantity": v} for v in ("10", "20", "30")]
    # Image: correct, distinct PO numbers on every row.
    vision_rows = [
        {"item_po_no": p, "item_quantity": v}
        for p, v in zip(("PO-1", "PO-2", "PO-3"), ("10", "20", "30"))
    ]

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=text_rows), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", return_value=vision_rows):
        run_extraction(db_session, job)

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "item_po_no")
            .order_by(JobFieldValue.row_index).all())
    assert [r.extracted_value for r in rows] == ["PO-1", "PO-2", "PO-3"]

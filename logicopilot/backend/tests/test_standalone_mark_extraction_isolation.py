"""Two or more is_multi_value marks on the SAME document used to be batched into ONE AI call
with ONE shared row count - exactly right for fields that genuinely describe the same line
(item_material_code + product_description must stay aligned). But a mark ticked
standalone_multi_value (container_number on a Bill of Lading) has nothing to do with any
other per-row field's row count. Batching it anyway meant adding a SECOND standalone field
(container_type) silently dragged the FIRST one's row count to match - a real, observed
regression: container_number, which read exactly 4 real containers on its own, went back to
reading 8 (re-admitting seal numbers) purely because container_type joined the same batch.
Each standalone mark must be its own, fully independent extract_document_rows call."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _row_values(db_session, job, label):
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == label)
        .order_by(JobFieldValue.row_index)
        .all()
    )
    return [(r.row_index, r.extracted_value) for r in rows]


def _empty_ocr_patches():
    return (
        patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some text", "text": "some text", "tokens": []}),
        patch("app.api.v1.jobs.extract_document_fields", return_value={}),
    )


def test_standalone_marks_are_never_batched_with_each_other_or_the_line_item_family(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(invoice)
    db_session.add(bl)
    db_session.commit()
    db_session.refresh(invoice)
    db_session.refresh(bl)

    for label in ("item_material_code", "product_description"):
        db_session.add(FieldMark(tenant_id=tenant.id, document_id=invoice.id, label_name=label,
                                 page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True))
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="container_number",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                             is_multi_value=True, standalone_multi_value=True))
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=bl.id, label_name="container_type",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1,
                             is_multi_value=True, standalone_multi_value=True))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ISOLATION1", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    for tdoc in (invoice, bl):
        db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                                   file_path="fake/does-not-exist.pdf", page_count=1))
    db_session.commit()

    calls: list[list[str]] = []

    def fake_extract_document_rows(ocr_text, row_specs, detected_row_count=None):
        labels = sorted(f["label"] for f in row_specs)
        calls.append(labels)
        if labels == ["item_material_code", "product_description"]:
            return [
                {"item_material_code": "MC1", "product_description": "WIDGET 1"},
                {"item_material_code": "MC2", "product_description": "WIDGET 2"},
            ]
        if labels == ["container_number"]:
            return [{"container_number": "WHSU2267116"},
                    {"container_number": "WHSU5514544"},
                    {"container_number": "WHSU5635546"},
                    {"container_number": "WHSU5850169"}]
        if labels == ["container_type"]:
            return [{"container_type": "20 SD"}, {"container_type": "40 HC"},
                    {"container_type": "40 HC"}, {"container_type": "40 HC"}]
        raise AssertionError(f"unexpected batch: {labels}")

    p1, p2 = _empty_ocr_patches()
    with p1, p2, patch("app.api.v1.jobs.extract_document_rows", side_effect=fake_extract_document_rows):
        run_extraction(db_session, job)

    # Three independent calls: the row-aligned Invoice pair stays batched together exactly as
    # before, and EACH standalone mark gets its own call - never combined with each other.
    assert sorted(calls) == [
        ["container_number"], ["container_type"], ["item_material_code", "product_description"],
    ]
    assert _row_values(db_session, job, "item_material_code") == [(1, "MC1"), (2, "MC2")]
    assert _row_values(db_session, job, "container_number") == [
        (1, "WHSU2267116"), (2, "WHSU5514544"), (3, "WHSU5635546"), (4, "WHSU5850169"),
    ]
    assert _row_values(db_session, job, "container_type") == [
        (1, "20 SD"), (2, "40 HC"), (3, "40 HC"), (4, "40 HC"),
    ]

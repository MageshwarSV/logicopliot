"""Product lines for a template whose description field is not called product_description
(Sea Export's "description", 2026-11-27): its jobs had no lines at all - no Combined
Description, no per-line custom fields. Lines now come from the per-line document boxes; a
table of its own (containers) never counts, and product_description still wins when present.
"""
from app.api.v1.jobs import _product_line_keys
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _setup(db, *, with_product_description=False):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Sea Export", status="approved")
    db.add(g); db.flush()
    d = TemplateDocument(tenant_id=t.id, group_id=g.id, name="INVOICE", doc_type="Invoice")
    db.add(d); db.flush()
    desc = FieldMark(tenant_id=t.id, document_id=d.id, label_name="description", page_number=1,
                     x=0.1, y=0.1, width=0.3, height=0.1, is_multi_value=True)
    cont = FieldMark(tenant_id=t.id, document_id=d.id, label_name="container_number", page_number=1,
                     x=0.1, y=0.5, width=0.3, height=0.1, is_multi_value=True, standalone_multi_value=True)
    head = FieldMark(tenant_id=t.id, document_id=d.id, label_name="invoice number", page_number=1,
                     x=0.6, y=0.1, width=0.2, height=0.05)
    db.add_all([desc, cont, head]); db.flush()
    j = Job(tenant_id=t.id, group_id=g.id, reference="JOB-LINES", status="extracted")
    db.add(j); db.flush()
    rows = [JobFieldValue(tenant_id=t.id, job_id=j.id, mark_id=desc.id, label_name="description",
                          row_index=r, extracted_value=f"item {r}") for r in (1, 2, 3)]
    rows += [JobFieldValue(tenant_id=t.id, job_id=j.id, mark_id=cont.id, label_name="container_number",
                           row_index=r, extracted_value=f"CONT{r}") for r in (1, 2, 3, 4, 5)]
    rows.append(JobFieldValue(tenant_id=t.id, job_id=j.id, mark_id=head.id, label_name="invoice number",
                              extracted_value="EX1"))
    if with_product_description:
        rows += [JobFieldValue(tenant_id=t.id, job_id=j.id, label_name="product_description",
                               row_index=r, extracted_value="x") for r in (1, 2)]
    db.add_all(rows); db.commit()
    return j


def test_lines_come_from_the_per_line_boxes(db_session):
    j = _setup(db_session)
    assert _product_line_keys(db_session, j) == {(1, 1), (1, 2), (1, 3)}   # 5 containers not counted


def test_product_description_still_decides_when_present(db_session):
    j = _setup(db_session, with_product_description=True)
    assert _product_line_keys(db_session, j) == {(1, 1), (1, 2)}

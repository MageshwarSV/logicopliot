"""A per-row CUSTOM field's own slot count must come from product_description's rows, not
from every row-indexed mark on the job. A container's own per-row marks (container_type,
container_number) use the SAME row_index numbering for a completely different count - how
many containers, not how many products - and a job can easily have more (or fewer) containers
than products. Found live on JOB-0A7B6C: 1 real product but 2 containers, which handed
Dump CTH Number/Document CTH a phantom second slot that matched no real product at all, while
a genuinely container-keyed field (ContainerTypeCode) correctly needs one slot per container."""
from unittest.mock import patch

from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    product_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="product_description",
                             page_number=1, x=0.1, y=0.1, width=0.2, height=0.05, is_multi_value=True)
    # standalone_multi_value: NOT row-aligned with the product table - extracted via its own
    # independent pass, which is exactly why its own row count can differ from the product
    # count in the first place (see run_extraction's mark_groups split).
    container_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="container_type",
                               page_number=1, x=0.1, y=0.2, width=0.2, height=0.05, is_multi_value=True,
                               standalone_multi_value=True)
    db_session.add_all([product_mark, container_mark])
    db_session.commit()

    # Keyed on a PRODUCT field - must get exactly one slot per real product.
    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                      kind="lookup", lookup_key_label="product_description", per_row=True)
    # Keyed on the CONTAINER field - must get one slot per container, even though that
    # outnumbers the job's real products.
    container_code = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ContainerTypeCode",
                                 kind="lookup", lookup_key_label="container_type", per_row=True,
                                 skip_material_master=True)
    db_session.add_all([cth, container_code])
    db_session.commit()
    db_session.refresh(cth)
    db_session.refresh(container_code)
    return group, tdoc, cth, container_code


def _make_job(db_session, tenant, group, tdoc, reference="JOB-1PRODUCT-2CONTAINERS"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference,
             status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/inv.pdf", page_count=1, file_index=0)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    return job, jd


def test_a_product_keyed_field_gets_one_slot_per_product_not_per_container(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, cth, container_code = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc)

    def _ocr_for(job_doc_dir, page):
        return {"layout_text": "Invoice", "text": "Invoice", "tokens": []}

    def _fields_for(ocr_text, fields, image_paths=None):
        return {}

    def _rows_for(ocr_text, row_specs, detected_row_count=None):
        labels = [spec["label"] for spec in row_specs]
        if "product_description" in labels:
            return [{"product_description": "WIDGET"}]  # one real product
        if "container_type" in labels:
            return [{"container_type": "40 HC"}, {"container_type": "40 RHC"}]  # two containers
        return []

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields", side_effect=_fields_for), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=_rows_for):
        run_extraction(db_session, job)

    cth_rows = (db_session.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cth.id).all())
    container_rows = (db_session.query(JobFieldValue)
                      .filter(JobFieldValue.job_id == job.id,
                              JobFieldValue.custom_field_id == container_code.id).all())

    # The product-keyed field: exactly one slot, matching the one real product - no phantom
    # second slot from the two containers.
    assert len(cth_rows) == 1
    assert cth_rows[0].row_index == 1

    # The container-keyed field: one slot per container, correctly MORE than the product count.
    assert len(container_rows) == 2
    assert sorted(r.row_index for r in container_rows) == [1, 2]


def test_a_container_keyed_field_gets_one_slot_per_container_not_per_product(db_session):
    """The real bug (JOB-3DA007): 5 products, 1 container. A container-keyed field must get
    exactly ONE slot (matching the one real container), never padded out to 5 slots to match
    the product count - a padded slot has no container to belong to, nowhere on the job
    screen it could ever render (Container Detail only ever shows real container rows), yet
    used to still count as "needs typing" and block the job regardless."""
    tenant = make_tenant(db_session)
    group, tdoc, cth, container_code = _make_template(db_session, tenant)
    job, jd = _make_job(db_session, tenant, group, tdoc)

    def _ocr_for(job_doc_dir, page):
        return {"layout_text": "Invoice", "text": "Invoice", "tokens": []}

    def _fields_for(ocr_text, fields, image_paths=None):
        return {}

    def _rows_for(ocr_text, row_specs, detected_row_count=None):
        labels = [spec["label"] for spec in row_specs]
        if "product_description" in labels:
            return [{"product_description": f"WIDGET {i}"} for i in range(5)]  # five products
        if "container_type" in labels:
            return [{"container_type": "40 HC"}]  # one container
        return []

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields", side_effect=_fields_for), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=_rows_for):
        run_extraction(db_session, job)

    cth_rows = (db_session.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cth.id).all())
    container_rows = (db_session.query(JobFieldValue)
                      .filter(JobFieldValue.job_id == job.id,
                              JobFieldValue.custom_field_id == container_code.id).all())

    assert len(cth_rows) == 5
    assert len(container_rows) == 1
    assert container_rows[0].row_index == 1


def test_container_fields_are_blanked_for_anything_other_than_fcl(db_session):
    """Container Type and ContainerTypeCode only ever apply to a Full Container Load
    shipment - an LCL (or any other) shipment's cargo shares someone else's container, so
    nothing about a container belongs on this customer's own paperwork, in the UI or the
    Excel export either. Confirmed by forcing a real value through extraction, then checking
    it was blanked anyway because Consignment Type read "LCL"."""
    tenant = make_tenant(db_session)
    group, tdoc, cth, container_code = _make_template(db_session, tenant)
    shipment_type_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id,
                                   label_name="Consignment Type", page_number=1,
                                   x=0.1, y=0.3, width=0.2, height=0.05, is_multi_value=False)
    db_session.add(shipment_type_mark)
    db_session.commit()
    job, jd = _make_job(db_session, tenant, group, tdoc, reference="JOB-LCL-SHIPMENT")

    def _ocr_for(job_doc_dir, page):
        return {"layout_text": "Invoice", "text": "Invoice", "tokens": []}

    def _fields_for(ocr_text, fields, image_paths=None):
        return {"Consignment Type": "LCL"}

    def _rows_for(ocr_text, row_specs, detected_row_count=None):
        labels = [spec["label"] for spec in row_specs]
        if "product_description" in labels:
            return [{"product_description": "WIDGET"}]
        if "container_type" in labels:
            return [{"container_type": "40 HC"}]
        return []

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields", side_effect=_fields_for), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=_rows_for):
        run_extraction(db_session, job)

    container_mark_rows = (db_session.query(JobFieldValue)
                           .filter(JobFieldValue.job_id == job.id,
                                   JobFieldValue.label_name == "container_type").all())
    container_code_rows = (db_session.query(JobFieldValue)
                           .filter(JobFieldValue.job_id == job.id,
                                   JobFieldValue.custom_field_id == container_code.id).all())

    assert len(container_mark_rows) == 1
    assert container_mark_rows[0].value == ""
    assert len(container_code_rows) == 1
    assert container_code_rows[0].value == ""


def test_container_fields_are_kept_for_an_fcl_shipment(db_session):
    tenant = make_tenant(db_session)
    group, tdoc, cth, container_code = _make_template(db_session, tenant)
    shipment_type_mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id,
                                   label_name="Consignment Type", page_number=1,
                                   x=0.1, y=0.3, width=0.2, height=0.05, is_multi_value=False)
    db_session.add(shipment_type_mark)
    db_session.commit()
    job, jd = _make_job(db_session, tenant, group, tdoc, reference="JOB-FCL-SHIPMENT")

    def _ocr_for(job_doc_dir, page):
        return {"layout_text": "Invoice", "text": "Invoice", "tokens": []}

    def _fields_for(ocr_text, fields, image_paths=None):
        return {"Consignment Type": "FCL"}

    def _rows_for(ocr_text, row_specs, detected_row_count=None):
        labels = [spec["label"] for spec in row_specs]
        if "product_description" in labels:
            return [{"product_description": "WIDGET"}]
        if "container_type" in labels:
            return [{"container_type": "40 HC"}]
        return []

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields", side_effect=_fields_for), \
         patch("app.api.v1.jobs.extract_document_rows", side_effect=_rows_for):
        run_extraction(db_session, job)

    container_mark_rows = (db_session.query(JobFieldValue)
                           .filter(JobFieldValue.job_id == job.id,
                                   JobFieldValue.label_name == "container_type").all())

    assert len(container_mark_rows) == 1
    assert container_mark_rows[0].value == "40 HC"

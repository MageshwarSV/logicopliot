"""entry_values_and_rows() unions every per-row label's (set, row) keys into one shared
line_keys list, so an invoice-line field and a packing-list field that describe the SAME
physical line stay aligned on the SAME row. A custom field ticked multi_value_from_document
(e.g. "Container No" reading every container off a bill of lading) is a different thing
entirely - it was never tied to any invoice line to begin with, so forcing it into that same
union either truncates it to the invoice's own line count or pads it with stray blank rows,
and can even add phantom extra rows to the invoice-line sheets when the document happens to
have MORE rows than the invoice does. This is its own list, sized to its own row count."""
from app.api.v1.jobs import entry_values_and_rows
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_group_and_job(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="item_material_code",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-STANDALONE1", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return group, job


def _add_row(db_session, job, label, row_index, value, *, set_index=None, custom_field_id=None):
    db_session.add(JobFieldValue(
        tenant_id=job.tenant_id, job_id=job.id, label_name=label, row_index=row_index,
        set_index=set_index, extracted_value=value, custom_field_id=custom_field_id,
    ))


def test_standalone_multi_value_field_keeps_its_own_row_count(db_session):
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    container_cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Container No", kind="ai",
        multi_value_from_document=True, ai_prompt="every container number",
    )
    db_session.add(container_cf)
    db_session.commit()
    db_session.refresh(container_cf)

    # 5 invoice line items (the real line-item family) ...
    for i in range(1, 6):
        _add_row(db_session, job, "item_material_code", i, f"MC{i}", set_index=1)
    # ... but only 2 containers on the bill of lading.
    _add_row(db_session, job, "Container No", 1, "SNBU2369717", custom_field_id=container_cf.id)
    _add_row(db_session, job, "Container No", 2, "REGU5094361", custom_field_id=container_cf.id)
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)

    assert rows["item_material_code"] == ["MC1", "MC2", "MC3", "MC4", "MC5"]
    assert rows["Container No"] == ["SNBU2369717", "REGU5094361"]
    from app.core.excel_entry import LINE_SET_KEY
    # The invoice's own line list must not have grown extra rows just because the bill of
    # lading happened to carry container data too.
    assert len(rows[LINE_SET_KEY]) == 5


def test_standalone_field_with_more_rows_than_the_invoice_does_not_inflate_item_rows(db_session):
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    container_cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Container No", kind="ai",
        multi_value_from_document=True, ai_prompt="every container number",
    )
    db_session.add(container_cf)
    db_session.commit()
    db_session.refresh(container_cf)

    # Only 1 invoice line, but 4 containers.
    _add_row(db_session, job, "item_material_code", 1, "MC1", set_index=1)
    for i in range(1, 5):
        _add_row(db_session, job, "Container No", i, f"CONT{i}", custom_field_id=container_cf.id)
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)

    assert rows["item_material_code"] == ["MC1"]
    assert rows["Container No"] == ["CONT1", "CONT2", "CONT3", "CONT4"]


def test_no_standalone_fields_behaves_exactly_as_before(db_session):
    """No multi_value_from_document field on this job at all - the ordinary single shared
    line_keys path, byte for byte as it already worked."""
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    for i in range(1, 4):
        _add_row(db_session, job, "item_material_code", i, f"MC{i}", set_index=1)
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)
    assert rows["item_material_code"] == ["MC1", "MC2", "MC3"]

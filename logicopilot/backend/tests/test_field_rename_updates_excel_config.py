"""Endpoint-level wiring for rename_field_in_excel_config (see its own unit tests in
test_excel_entry_audit_fixes.py): PATCH /marks/{id} and PATCH /custom-fields/{id}, when the
rename actually changes label_name, must update the owning TemplateGroup's excel_config so
edit_custom_field's own documented promise ("Renaming is safe") is actually true.

Also covers the companion fix: a job extracted BEFORE a custom field's rename keeps its own
stored label_name snapshot forever unless something carries the rename onto it too - found
live when "RITC No." was renamed to "CTH": the Excel export matches a column to a value by
this exact label string, so an already-extracted job's line exported blank in that column
even though the value was sitting right there under the old label. PATCH /custom-fields/{id}
now backfills every existing JobFieldValue under that field the same rename hits, and
POST /custom-fields/{id}/backfill-label is the one-off catch-up for a field already renamed
before this existed."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _login_super_admin(client, db_session, email="sa-rename@example.com"):
    make_user(db_session, role=SUPER_ADMIN, email=email)
    login(client, email)


def _make_group_with_excel_config(db_session, config):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved",
                          entry_mode="excel", excel_config=config)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return tenant, group


def test_renaming_a_mark_updates_its_excel_column_mapping(client, db_session):
    tenant, group = _make_group_with_excel_config(db_session, {
        "sheet": "GENERAL", "header_row": 1,
        "columns": [{"column": "A", "field": "hbl_no"}],
    })
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="bl")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="hbl_no",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    _login_super_admin(client, db_session)
    resp = client.patch(f"/api/v1/marks/{mark.id}", json={"label_name": "house_bl_number"})
    assert resp.status_code == 200, resp.text

    db_session.refresh(group)
    assert group.excel_config["columns"][0]["field"] == "house_bl_number"


def test_renaming_a_custom_field_updates_its_excel_column_mapping(client, db_session):
    tenant, group = _make_group_with_excel_config(db_session, {
        "sheets": [{"sheet": "GENERAL", "columns": [{"column": "B", "field": "insurance_pct"}]}],
    })
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="insurance_pct",
                     kind="hardcoded", hardcoded_value="0.5")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    _login_super_admin(client, db_session)
    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"label_name": "insurance_rate"})
    assert resp.status_code == 200, resp.text

    db_session.refresh(group)
    assert group.excel_config["sheets"][0]["columns"][0]["field"] == "insurance_rate"


def test_renaming_a_custom_field_backfills_existing_job_values_label(client, db_session):
    tenant, group = _make_group_with_excel_config(db_session, {
        "sheets": [{"sheet": "ITEMS", "columns": [{"column": "J", "field": "CTH"}]}],
    })
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="RITC No.", kind="lookup")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-RENAME-BACKFILL", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    # Extracted BEFORE the rename - its own stored label is a snapshot of what the field was
    # called at the time (see this field's own run_extraction write site).
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                       label_name="RITC No.", extracted_value="87089900", row_index=1)
    db_session.add(fv)
    db_session.commit()

    _login_super_admin(client, db_session)
    resp = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"label_name": "CTH"})
    assert resp.status_code == 200, resp.text

    db_session.refresh(fv)
    assert fv.label_name == "CTH"
    assert fv.extracted_value == "87089900"  # the actual data is untouched


def test_backfill_label_endpoint_catches_up_a_field_already_renamed(client, db_session):
    """The one-off correction for a rename that happened before the auto-backfill above
    existed - re-applies the field's CURRENT label onto every stale JobFieldValue."""
    tenant, group = _make_group_with_excel_config(db_session, {"sheets": []})
    # Already renamed at the CustomField level, but its job's own stored value was never
    # carried along - exactly the gap found live on "CTH" (was "RITC No.").
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH", kind="lookup")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-BACKFILL-ENDPOINT", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    stale = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                          label_name="RITC No.", extracted_value="87089900", row_index=1)
    current = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                            label_name="CTH", extracted_value="12345678", row_index=2)
    db_session.add_all([stale, current])
    db_session.commit()

    _login_super_admin(client, db_session)
    resp = client.post(f"/api/v1/custom-fields/{cf.id}/backfill-label")
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1  # only the stale one needed touching

    db_session.refresh(stale)
    db_session.refresh(current)
    assert stale.label_name == "CTH"
    assert stale.extracted_value == "87089900"
    assert current.label_name == "CTH"  # already correct - untouched either way


def test_editing_a_mark_without_changing_its_label_leaves_excel_config_untouched(client, db_session):
    tenant, group = _make_group_with_excel_config(db_session, {
        "sheet": "GENERAL", "header_row": 1,
        "columns": [{"column": "A", "field": "hbl_no"}],
    })
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="bl")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="hbl_no",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)

    _login_super_admin(client, db_session)
    resp = client.patch(f"/api/v1/marks/{mark.id}", json={"semantic_description": "House BL number"})
    assert resp.status_code == 200, resp.text

    db_session.refresh(group)
    assert group.excel_config["columns"][0]["field"] == "hbl_no"

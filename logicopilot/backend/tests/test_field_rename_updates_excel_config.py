"""Endpoint-level wiring for rename_field_in_excel_config (see its own unit tests in
test_excel_entry_audit_fixes.py): PATCH /marks/{id} and PATCH /custom-fields/{id}, when the
rename actually changes label_name, must update the owning TemplateGroup's excel_config so
edit_custom_field's own documented promise ("Renaming is safe") is actually true."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
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

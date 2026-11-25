"""Customer Onboarding -> the live template (app/api/v1/onboarding_template.py).

The new onboarding screens edit the REAL template jobs run on: an onboarding field is linked to
the live field its ERP Excel column reads, a Document Mapping box becomes (or moves) the live
box, a Field Configuration field becomes a live custom field, and every ERP Excel column rule
excel_entry honours is set and validated on TemplateGroup.excel_config."""
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.models.custom_field import CustomField
from app.models.erp_mapping import ErpDocSample, ErpField, ErpWorkbook
from app.models.field_mark import FieldMark
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path))
    return tmp_path


@pytest.fixture()
def setup(db_session, client, uploads):
    tenant = make_tenant(db_session, name="4S")
    admin = make_user(db_session, role="super_admin", email="sa@example.com")
    assert login(client, admin.email).status_code == 200
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved", entry_mode="excel",
                          excel_config={"source": "template", "file_name": "imp.xlsx", "sheets": [
                              {"sheet": "GENERAL", "header_row": 1, "scope": "job", "columns": [
                                  {"column": "I", "header": "PortOfShipmentCode", "field": "Shipment Port Code"},
                              ]},
                              {"sheet": "ITEMS", "header_row": 1, "scope": "line", "columns": [
                                  {"column": "AF", "header": "IGST_LevyNotnFlag", "field": "", "value": "+"},
                              ]},
                          ]})
    db_session.add(group)
    db_session.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="BL",
                           order_index=0, page_count=0)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice",
                           order_index=1, page_count=0)
    db_session.add_all([awb, inv])
    db_session.flush()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Shipment Port Code", kind="hardcoded")
    db_session.add(cf)
    sample = uploads / "sample.png"
    Image.new("RGB", (400, 300), "white").save(sample)
    wb = ErpWorkbook(tenant_id=tenant.id, group_id=group.id, file_name="imp.xlsx", stored_path="x",
                     sheets=[{"name": "GENERAL", "header_row": 1, "columns": []},
                             {"name": "ITEMS", "header_row": 1, "columns": []}],
                     documents=[{"id": awb.id, "name": "Air Waybill", "template_document_id": awb.id},
                                {"id": inv.id, "name": "Invoice", "template_document_id": inv.id}])
    db_session.add(wb)
    db_session.flush()
    folder = uploads / "onb" / "awb"
    folder.mkdir(parents=True)
    (folder / "original.png").write_bytes(sample.read_bytes())
    db_session.add(ErpDocSample(workbook_id=wb.id, doc_id=awb.id, file_name="awb.png", stored_dir=str(folder), page_count=1))
    port = ErpField(workbook_id=wb.id, group_id=group.id, sheet_name="GENERAL", column_key="I",
                    excel_name="PortOfShipmentCode", category="reference")
    hawb = ErpField(workbook_id=wb.id, group_id=group.id, sheet_name="GENERAL", column_key="B",
                    excel_name="HAWB_No", category="document", source_document=awb.id)
    flag = ErpField(workbook_id=wb.id, group_id=group.id, sheet_name="ITEMS", column_key="AF",
                    excel_name="IGST_LevyNotnFlag", category="fixed", config={"value": "+"})
    origin = ErpField(workbook_id=wb.id, group_id=group.id, sheet_name="GENERAL", column_key="H",
                      excel_name="CountryOfOriginCode", category="ai", ai_prompt="Most common origin, 2 letters")
    db_session.add_all([port, hawb, flag, origin])
    db_session.commit()
    return {"tenant": tenant, "group": group, "awb": awb, "inv": inv, "cf": cf, "wb": wb,
            "port": port, "hawb": hawb, "flag": flag, "origin": origin}


def _col(db_session, group_id, sheet, column):
    db_session.expire_all()
    cfg = db_session.get(TemplateGroup, group_id).excel_config
    plan = next(p for p in cfg["sheets"] if p["sheet"] == sheet)
    return next((c for c in plan["columns"] if c.get("column") == column), None)


def test_links_follow_the_excel_mapping(client, setup):
    r = client.get(f"/api/v1/onboarding/template/groups/{setup['group'].id}/links")
    assert r.status_code == 200, r.text
    by = {f["excel_name"]: f for f in r.json()["fields"]}
    assert by["PortOfShipmentCode"]["ref"] == f"c:{setup['cf'].id}"
    assert by["PortOfShipmentCode"]["label"] == "Shipment Port Code"
    assert by["HAWB_No"]["ref"] is None and not by["HAWB_No"]["in_mapping"]
    assert by["IGST_LevyNotnFlag"]["column_rule"] == {"field": "", "value": "+"}
    names = {n["name"]: n["present"] for n in r.json()["special_names"]}
    assert names["Shipment Port Code"] is True and names["branch_name"] is False


def test_column_rules_are_validated_and_saved(client, db_session, setup):
    gid = setup["group"].id
    url = f"/api/v1/onboarding/template/groups/{gid}/excel-column"
    ok = client.patch(url, json={"sheet": "ITEMS", "column": "AF", "header": "IGST_LevyNotnFlag",
                                 "rule": {"value": "G", "max_len": 1, "fallback_fields": ["Shipment Port Code"]}})
    assert ok.status_code == 200, ok.text
    col = _col(db_session, gid, "ITEMS", "AF")
    assert col["value"] == "G" and col["max_len"] == 1 and col["fallback_fields"] == ["Shipment Port Code"]

    # null removes a key, other keys stay
    client.patch(url, json={"sheet": "ITEMS", "column": "AF", "rule": {"max_len": None}})
    col = _col(db_session, gid, "ITEMS", "AF")
    assert "max_len" not in col and col["value"] == "G"

    bad = [
        {"field": "No Such Field"},
        {"field": "#page"},
        {"transform": "upper"},
        {"from_document": "Bill of Lading"},
        {"max_len": 0},
        {"pick_by": {"field": "Shipment Port Code", "options": {}}},
        {"colour": "red"},
    ]
    for rule in bad:
        r = client.patch(url, json={"sheet": "ITEMS", "column": "AF", "rule": rule})
        assert r.status_code == 422, (rule, r.text)
    assert client.patch(url, json={"sheet": "NOPE", "column": "A", "rule": {"value": "x"}}).status_code == 409

    # every key excel_entry reads is accepted
    good = client.patch(url, json={"sheet": "ITEMS", "column": "AG", "header": "IGST_ExemptionNotnType",
                                   "rule": {"field": "#row", "values": ["1", "2"], "transform": "leading_digits",
                                            "from_document": "Air Waybill"}})
    assert good.status_code == 200, good.text
    assert _col(db_session, gid, "ITEMS", "AG")["from_document"] == "Air Waybill"


def test_sheet_plans(client, db_session, setup):
    gid = setup["group"].id
    url = f"/api/v1/onboarding/template/groups/{gid}/excel-sheet"
    assert client.patch(url, json={"sheet": "ITEMS", "scope": "invoice", "header_row": 2}).status_code == 200
    assert client.patch(url, json={"sheet": "ITEMS", "scope": "everything"}).status_code == 422
    assert client.patch(url, json={"sheet": "INVOICES", "scope": "invoice"}).status_code == 200
    db_session.expire_all()
    plans = {p["sheet"]: p for p in db_session.get(TemplateGroup, gid).excel_config["sheets"]}
    assert plans["ITEMS"]["scope"] == "invoice" and plans["ITEMS"]["header_row"] == 2
    assert plans["INVOICES"]["columns"] == []
    assert client.patch(url, json={"sheet": "INVOICES", "remove": True}).status_code == 200


def test_legacy_single_sheet_config_is_kept_equivalent(client, db_session, setup):
    group = db_session.get(TemplateGroup, setup["group"].id)
    group.excel_config = {"source": "template", "sheet": "Import", "header_row": 1,
                          "columns": [{"column": "A", "header": "Qty", "field": "Shipment Port Code"}]}
    db_session.commit()
    r = client.patch(f"/api/v1/onboarding/template/groups/{group.id}/excel-column",
                     json={"sheet": "Import", "column": "A", "rule": {"max_len": 3}})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    cfg = db_session.get(TemplateGroup, group.id).excel_config
    from app.core.excel_entry import sheet_plans

    plans = sheet_plans(cfg)
    assert plans[0]["scope"] == "line" and plans[0]["columns"][0]["max_len"] == 3


def test_drawing_a_box_creates_then_moves_the_live_box(client, db_session, setup, uploads):
    url = f"/api/v1/onboarding/template/erp-fields/{setup['hawb'].id}/template-box"
    r = client.post(url, json={"page": 1, "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] and body["label"] == "HAWB_No" and body["warning"] is None
    db_session.expire_all()
    awb = db_session.get(TemplateDocument, setup["awb"].id)
    assert awb.page_count == 1 and Path(awb.file_path).exists()   # the onboarding sample became the live one
    mark = db_session.get(FieldMark, body["mark_id"])
    assert (mark.page_number, mark.x, mark.width) == (1, 0.1, 0.3)
    assert _col(db_session, setup["group"].id, "GENERAL", "B")["field"] == "HAWB_No"   # column linked

    again = client.post(url, json={"page": 1, "x": 0.5, "y": 0.5, "width": 0.2, "height": 0.1}).json()
    assert not again["created"] and again["mark_id"] == body["mark_id"]
    db_session.expire_all()
    assert db_session.get(FieldMark, body["mark_id"]).x == 0.5
    assert client.post(url, json={"page": 1, "x": 0.9, "y": 0.1, "width": 0.3, "height": 0.1}).status_code == 422


def test_live_box_settings_edit_including_geometry(client, db_session, setup):
    body = client.post(f"/api/v1/onboarding/template/erp-fields/{setup['hawb'].id}/template-box",
                       json={"page": 1, "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05}).json()
    r = client.patch(f"/api/v1/marks/{body['mark_id']}", json={
        "tenant_format_prompt": "  Remove spaces and hyphens.  ", "correction_prompt": "Keep letters",
        "verify_with_other_document": True, "color": "blue", "x": 0.2, "page_number": None})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tenant_format_prompt"] == "Remove spaces and hyphens."
    assert out["verify_with_other_document"] is True and out["color"] == "blue" and out["x"] == 0.2
    assert out["page_number"] == 1  # null means "leave it"
    assert client.patch(f"/api/v1/marks/{body['mark_id']}", json={"x": 0.9}).status_code == 422


def test_field_configuration_creates_the_live_custom_field(client, db_session, setup):
    url = f"/api/v1/onboarding/template/erp-fields/{setup['origin'].id}/template-field"
    r = client.post(url, json={})
    assert r.status_code == 200, r.text
    cf = db_session.get(CustomField, r.json()["custom_field_id"])
    assert cf.kind == "ai" and cf.ai_prompt == "Most common origin, 2 letters" and cf.label_name == "CountryOfOriginCode"
    assert _col(db_session, setup["group"].id, "GENERAL", "H")["field"] == "CountryOfOriginCode"
    again = client.post(url, json={}).json()
    assert again["created"] is False and again["custom_field_id"] == cf.id

    fixed = client.post(f"/api/v1/onboarding/template/erp-fields/{setup['flag'].id}/template-field", json={}).json()
    fcf = db_session.get(CustomField, fixed["custom_field_id"])
    assert fcf.kind == "hardcoded" and fcf.hardcoded_value == "+"
    sep = client.patch(f"/api/v1/custom-fields/{fcf.id}", json={"composite_separators": [" / "]})
    assert sep.status_code == 200 and sep.json()["composite_separators"] == [" / "]


def test_link_to_an_existing_field_and_reject_unknown(client, db_session, setup):
    url = f"/api/v1/onboarding/template/erp-fields/{setup['hawb'].id}/link"
    assert client.put(url, json={"label": "Nope"}).status_code == 422
    assert client.put(url, json={"label": "Shipment Port Code"}).status_code == 200
    assert _col(db_session, setup["group"].id, "GENERAL", "B")["field"] == "Shipment Port Code"


def test_document_order_users_history_samples(client, db_session, setup):
    gid = setup["group"].id
    r = client.patch(f"/api/v1/onboarding/template/documents/{setup['inv'].id}/order", json={"order_index": 0})
    assert r.status_code == 200 and r.json()["order"] == [setup["inv"].id, setup["awb"].id]

    op = make_user(db_session, role="operator", tenant=setup["tenant"], email="op@example.com")
    rows = client.get(f"/api/v1/onboarding/template/groups/{gid}/users").json()
    me = next(u for u in rows if u["id"] == op.id)
    assert me["sees_all"] and not me["assigned"]
    assert client.put(f"/api/v1/onboarding/template/groups/{gid}/users/{op.id}", json={"assigned": True}).status_code == 200
    me = next(u for u in client.get(f"/api/v1/onboarding/template/groups/{gid}/users").json() if u["id"] == op.id)
    assert me["assigned"] and me["assignment_count"] == 1 and not me["sees_all"]
    assert client.put(f"/api/v1/onboarding/template/groups/{gid}/users/{op.id}", json={"assigned": False}).status_code == 200

    assert client.get(f"/api/v1/onboarding/template/groups/{gid}/prompt-history").json() == []
    assert client.get(f"/api/v1/onboarding/template/groups/{gid}/document-samples").json() == []


def test_super_admin_only(client, db_session, setup):
    gid = setup["group"].id
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    for method, url in (("get", f"/api/v1/onboarding/template/groups/{gid}/links"),
                        ("patch", f"/api/v1/onboarding/template/groups/{gid}/excel-sheet"),
                        ("get", f"/api/v1/onboarding/template/groups/{gid}/users")):
        r = getattr(client, method)(url, **({"json": {"sheet": "ITEMS"}} if method == "patch" else {}))
        assert r.status_code in (401, 403), (url, r.status_code)
    tadmin = make_user(db_session, role="tenant_admin", tenant=setup["tenant"], email="ta@example.com")
    if login(client, tadmin.email).status_code == 200:
        assert client.get(f"/api/v1/onboarding/template/groups/{gid}/links").status_code == 403

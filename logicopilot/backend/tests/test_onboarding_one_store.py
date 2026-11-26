"""Recommended workflow, phase A - one data store: what onboarding configures is what the live
template (and so every job) uses."""
from app.models.custom_field import CustomField
from app.models.erp_mapping import ErpFieldMark
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.test_onboarding_live_template import setup, uploads  # noqa: F401  (fixtures)

ONB = "/api/v1/onboarding/erp/fields"
TPL = "/api/v1/onboarding/template/erp-fields"


def test_step4_save_goes_onto_the_live_field(client, db_session, setup):
    flag = setup["flag"]
    # not connected yet: saved in onboarding only, and the reply says so
    r = client.patch(f"{ONB}/{flag.id}", json={"config": {"value": "G"}})
    assert r.status_code == 200, r.text and r.json()["live"] is None
    cf_id = client.post(f"{TPL}/{flag.id}/template-field", json={}).json()["custom_field_id"]
    # connected: a later Step 4 change reaches the live field
    r = client.patch(f"{ONB}/{flag.id}", json={"config": {"value": "+", "hidden": True}, "display_label": "IGST flag"})
    assert r.status_code == 200, r.text
    assert r.json()["live"]["ref"] == f"c:{cf_id}"
    db_session.expire_all()
    cf = db_session.get(CustomField, cf_id)
    assert cf.hardcoded_value == "+" and cf.hidden_from_capture is True and cf.display_label == "IGST flag"


def test_step4_does_not_blank_an_ai_rule(client, db_session, setup):
    origin = setup["origin"]
    cf_id = client.post(f"{TPL}/{origin.id}/template-field", json={}).json()["custom_field_id"]
    client.patch(f"/api/v1/custom-fields/{cf_id}", json={"ai_prompt": "Written in the editor"})
    origin.ai_prompt = None
    db_session.commit()
    assert client.patch(f"{ONB}/{origin.id}", json={"display_label": "Origin"}).status_code == 200
    db_session.expire_all()
    assert db_session.get(CustomField, cf_id).ai_prompt == "Written in the editor"


def _box(client, db_session, setup):
    hawb, wb = setup["hawb"], setup["wb"]
    mark_id = client.post(f"{TPL}/{hawb.id}/template-box",
                          json={"page": 1, "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05}).json()["mark_id"]
    db_session.add(ErpFieldMark(workbook_id=wb.id, field_id=hawb.id, doc_id=setup["awb"].id,
                                x=0.1, y=0.2, width=0.3, height=0.05))
    db_session.commit()
    return hawb, mark_id


def test_line_item_reaches_the_live_box(client, db_session, setup):
    hawb, mark_id = _box(client, db_session, setup)
    r = client.patch(f"{ONB}/{hawb.id}/mark", json={"line_item": True})
    assert r.status_code == 200, r.text and r.json()["live_updated"] is True
    db_session.expire_all()
    assert db_session.get(FieldMark, mark_id).is_multi_value is True


def test_clear_box_keeps_the_live_box_on_a_live_template(client, db_session, setup):
    hawb, mark_id = _box(client, db_session, setup)          # the fixture's template is approved (live)
    r = client.delete(f"{ONB}/{hawb.id}/mark").json()
    assert "kept" in r["message"]
    db_session.expire_all()
    assert db_session.get(FieldMark, mark_id) is not None


def test_clear_box_keeps_a_box_jobs_have_read(client, db_session, setup):
    hawb, mark_id = _box(client, db_session, setup)
    group = db_session.get(TemplateGroup, setup["group"].id)
    group.status = "draft"
    job = Job(tenant_id=setup["tenant"].id, group_id=group.id, reference="J1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.add(JobFieldValue(tenant_id=setup["tenant"].id, job_id=job.id, mark_id=mark_id,
                                 label_name="HAWB_No", extracted_value="X"))
    db_session.commit()
    assert "kept" in client.delete(f"{ONB}/{hawb.id}/mark").json()["message"]


def test_clear_box_removes_an_unused_live_box_on_a_draft_template(client, db_session, setup):
    hawb, mark_id = _box(client, db_session, setup)
    db_session.get(TemplateGroup, setup["group"].id).status = "draft"
    db_session.commit()
    r = client.delete(f"{ONB}/{hawb.id}/mark").json()
    assert "removed" in r["message"]
    db_session.expire_all()
    assert db_session.get(FieldMark, mark_id) is None


def test_ai_field_test_runs_the_saved_rule_on_the_samples(client, db_session, setup):
    from unittest.mock import patch

    from app.models.template_document import TemplateDocument

    origin = setup["origin"]
    cf_id = client.post(f"{TPL}/{origin.id}/template-field", json={}).json()["custom_field_id"]
    awb = db_session.get(TemplateDocument, setup["awb"].id)
    awb.page_count = 1
    db_session.commit()
    with patch("app.core.docai.get_page_ocr", return_value={"layout_text": "Origin: GB"}), \
         patch("app.core.extraction.extract_document_fields", return_value={"CountryOfOriginCode": "GB"}) as ex:
        r = client.post(f"/api/v1/onboarding/template/custom-fields/{cf_id}/test-ai")
    assert r.status_code == 200, r.text
    by = {x["document"]: x for x in r.json()["results"]}
    assert by["Air Waybill"]["value"] == "GB"
    assert by["Invoice"]["note"] == "no sample uploaded"
    assert ex.call_args[0][1][0]["prompt"] == "Most common origin, 2 letters"
    # no rule -> refused
    client.patch(f"/api/v1/custom-fields/{cf_id}", json={"ai_prompt": ""})
    assert client.post(f"/api/v1/onboarding/template/custom-fields/{cf_id}/test-ai").status_code == 422


def test_generate_prompts_fills_only_missing_and_logs_a_regenerate(client, db_session, setup):
    from unittest.mock import patch

    from app.models.prompt_change import PromptChange

    hawb, mark_id = _box(client, db_session, setup)
    gid = setup["group"].id
    url = f"/api/v1/onboarding/template/groups/{gid}/generate-prompts"

    def fake(mark, document, db):
        mark.extraction_prompt = f"Read {mark.label_name} v{(mark.extraction_prompt or '').count('v') + 1}"
        mark.anchor_variations = ["HAWB No", "House AWB"]
        db.commit()

    m = db_session.get(FieldMark, mark_id)
    m.extraction_prompt = None
    db_session.commit()
    with patch("app.api.v1.field_marks._apply_profile", side_effect=fake):
        r = client.post(url, json={}).json()
        assert r["generated"] == 1 and r["mark_ids"] == [mark_id]
        again = client.post(url, json={}).json()                 # already has one: left alone
        assert again["generated"] == 0 and again["skipped"] == 1
        assert db_session.query(PromptChange).filter(PromptChange.mark_id == mark_id).count() == 0
        regen = client.post(url, json={"mark_ids": [mark_id], "only_missing": False}).json()
        assert regen["generated"] == 1
    hist = db_session.query(PromptChange).filter(PromptChange.mark_id == mark_id).one()
    assert hist.action == "regenerate" and hist.old_prompt == "Read HAWB_No v1"


def test_end_to_end_test_compares_the_built_excel_with_the_customers(client, db_session, setup):
    from types import SimpleNamespace as NS
    from unittest.mock import patch

    from app.models.template_document import TemplateDocument

    group = db_session.get(TemplateGroup, setup["group"].id)
    group.excel_config = {"sheets": [{"sheet": "GENERAL", "header_row": 1, "scope": "job", "columns": [
        {"column": "I", "header": "PortOfShipmentCode", "field": "Shipment Port Code"},
        {"column": "B", "header": "HAWB_No", "field": "HAWB_No"},
        {"column": "H", "header": "CountryOfOriginCode", "field": "Origin AI"},
    ]}]}
    setup["cf"].hardcoded_value = "INMAA4"
    db_session.add(CustomField(tenant_id=setup["tenant"].id, group_id=group.id, label_name="Origin AI", kind="ai",
                               ai_prompt="x"))
    setup["port"].samples = ["INMAA4"]
    setup["hawb"].samples = ["H123"]
    setup["origin"].samples = ["GB"]
    db_session.get(TemplateDocument, setup["awb"].id).page_count = 1
    db_session.commit()

    demo = NS(results=[NS(label_name="HAWB_No", extracted_value="H999", extracted_values=None, mark_id="m1", matched_anchor=None)])
    with patch("app.api.v1.field_marks.demo_extract", return_value=demo):
        r = client.post(f"/api/v1/onboarding/template/groups/{group.id}/end-to-end-test")
    assert r.status_code == 200, r.text
    body = r.json()
    cols = {c["header"]: c for c in body["sheets"][0]["columns"]}
    assert cols["PortOfShipmentCode"]["status"] == "match"                  # fixed value
    assert cols["HAWB_No"]["status"] == "differs" and cols["HAWB_No"]["read_from"] == "Air Waybill"
    assert cols["CountryOfOriginCode"]["status"] == "on_job"                # AI: filled on a real job
    assert body["summary"]["match"] == 1 and body["summary"]["differs"] == 1 and body["summary"]["match_rate"] == 50
    assert "Invoice" in body["unread_documents"]

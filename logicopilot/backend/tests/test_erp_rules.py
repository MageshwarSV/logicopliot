"""ERP column rules (P1 + P2): country names converted per column, values the ERP would refuse
stopped before the ERP, Super Admin rules per template."""
import uuid

import openpyxl
import pytest

from app.core import erp_rules as er
from app.core.erp_masters import normalize_country
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user

CFG = {"sheets": [
    {"sheet": "GENERAL", "scope": "job", "columns": [
        {"header": "CountryOfOriginCode", "field": "Origin"},
        {"header": "OriginName", "field": "Origin"},          # same field, NOT a country-code column name
        {"header": "Currency", "field": "currency"},
    ]},
    {"sheet": "ITEMS", "scope": "line", "columns": [
        {"header": "ItemSrNo", "field": "#row"},
        {"header": "Desc", "field": "product_description"},
        {"header": "Country_of_Origin", "field": "CountryOfOriginCode"},
        {"header": "COO_Issuing_Country", "field": "CountryOfOriginCode"},
    ]},
]}


@pytest.mark.parametrize("raw,code", [("POLAND", "PL"), ("poland ", "PL"), ("Great Britain", "GB"), ("pl", "PL"),
                                      ("USA", "US"), ("Korea, Republic of", "KR"), ("Viet Nam", "VN")])
def test_country_names_become_codes_when_certain(raw, code):
    assert normalize_country(raw) == code


def test_uncertain_country_left_alone():
    assert normalize_country("Polska") == "Polska" and normalize_country("") == ""


def _job(db, origin="POLAND", coo=("POLAND", "Polska"), rules=None):
    t = make_tenant(db, name=f"T-{uuid.uuid4().hex[:5]}")
    g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved", entry_mode="excel",
                      excel_config=CFG, erp_column_rules=rules)
    db.add(g)
    db.flush()
    job = Job(tenant_id=t.id, group_id=g.id, reference=f"JOB-{uuid.uuid4().hex[:6]}", status="extracted")
    db.add(job)
    db.flush()
    db.add_all([JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Origin", extracted_value=origin),
                JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="currency", extracted_value="usd")])
    for row, val in enumerate(coo, start=1):
        db.add_all([
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="product_description", row_index=row, extracted_value=f"Item {row}"),
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="CountryOfOriginCode", row_index=row, extracted_value=val),
        ])
    db.commit()
    return t, g, job


def _values(db, job):
    from app.api.v1.jobs import entry_values_and_rows

    return entry_values_and_rows(db, job)


def test_conversion_is_per_column(db_session, tmp_path):
    from app.core.excel_entry import build_for_job

    _t, g, job = _job(db_session, coo=("POLAND", "china"))
    values, rows = _values(db_session, job)
    book = build_for_job(er.effective_config(g), values, rows, tmp_path)
    wb = openpyxl.load_workbook(book)
    general = [c.value for c in wb["GENERAL"][2]]
    assert general[0] == "PL" and general[1] == "POLAND"          # the name column keeps the name
    items = [[c.value for c in r] for r in wb["ITEMS"].iter_rows(min_row=2)]
    assert [r[2] for r in items] == ["PL", "CN"] and [r[3] for r in items] == ["PL", "CN"]


def test_check_reports_what_could_not_be_placed(db_session):
    _t, g, job = _job(db_session)
    values, rows = _values(db_session, job)
    problems = er.check(db_session, job, g, values, rows)
    assert {(p["sheet"], p["column"], p["product"]) for p in problems} == {
        ("ITEMS", "Country_of_Origin", 2), ("ITEMS", "COO_Issuing_Country", 2)}
    help_ = er.pre_check(db_session, job, values, rows)
    item = help_["items"][0]
    assert help_["kind"] == "pre_check" and item["fixable"] and item["sent"] == ["Polska"]
    assert item["erp_columns"] == ["Country_of_Origin", "COO_Issuing_Country"] and item["where"] == "Invoice 1 · Product 2"
    assert "2-letter country code" in item["needs"] and "Not sent to the ERP" in er.summary(help_)


def test_stored_rules_override_and_switch_off(db_session):
    rules = {"GENERAL|Currency": {"master": "currency"},
             "ITEMS|Country_of_Origin": {"off": True}, "ITEMS|COO_Issuing_Country": {"off": True},
             "ITEMS|Desc": {"required": True, "length": 3, "length_mode": "max"}}
    _t, g, job = _job(db_session, rules=rules)
    values, rows = _values(db_session, job)
    problems = er.check(db_session, job, g, values, rows)
    cols = {(p["column"], p["product"]) for p in problems}
    # usd is a valid currency (case-insensitive); country checks switched off; Desc too long
    assert cols == {("Desc", 1), ("Desc", 2)}
    assert problems[0]["rule"].startswith("At most 3 characters")


def test_erp_run_is_stopped_before_the_erp(db_session):
    from app.api.v1.jobs import ErpRulesFailed, _build_job_excel

    _t, _g, job = _job(db_session)
    with pytest.raises(ErpRulesFailed) as exc:
        _build_job_excel(db_session, job, check_rules=True)
    assert exc.value.help["items"][0]["sent"] == ["Polska"]
    # a download still builds (the operator can see the Excel)
    assert _build_job_excel(db_session, job).exists()


def test_super_admin_rules_api(client, db_session):
    _t, g, job = _job(db_session)
    _t2, other, _j2 = _job(db_session, rules={"GENERAL|Currency": {"master": "currency"}})
    sa = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@example.com")
    assert login(client, sa.email).status_code == 200
    assert any(t["id"] == g.id for t in client.get("/api/v1/erp-rules/templates").json())
    cols = {c["key"]: c for c in client.get(f"/api/v1/erp-rules/{g.id}").json()["columns"]}
    assert cols["ITEMS|Country_of_Origin"]["builtin"]["master"] == "country"
    assert cols["GENERAL|OriginName"]["builtin"] is None
    r = client.put(f"/api/v1/erp-rules/{g.id}", json={"rules": {"ITEMS|Desc": {"required": True, "length": "40"},
                                                                 "NOPE|x": {"required": True}}})
    assert r.json() == {"saved": 1}
    assert client.put(f"/api/v1/erp-rules/{g.id}", json={"rules": {"ITEMS|Desc": {"pattern": "("}}}).status_code == 422
    assert client.post(f"/api/v1/erp-rules/{g.id}/copy-from/{other.id}").json()["copied"] == 1
    db_session.expire_all()
    assert set(db_session.get(TemplateGroup, g.id).erp_column_rules) == {"GENERAL|Currency", "ITEMS|Desc"}
    chk = client.get(f"/api/v1/erp-rules/{g.id}/check", params={"reference": job.reference}).json()
    assert {p["column"] for p in chk["problems"]} == {"Country_of_Origin", "COO_Issuing_Country"}


# ---------------------------------------------------------------- currency, P3, P4

@pytest.mark.parametrize("raw,code", [("US$", "USD"), ("Rs.", "INR"), ("euro", "EUR"), ("usd", "USD"), ("RMB", "CNY")])
def test_currency_names_become_codes_when_certain(raw, code):
    from app.core.erp_masters import normalize_currency

    assert normalize_currency(raw) == code


def test_currency_column_converted_and_checked(db_session, tmp_path):
    from app.core.excel_entry import build_for_job

    _t, g, job = _job(db_session, coo=("CN", "CN"))
    fv = db_session.query(JobFieldValue).filter_by(job_id=job.id, label_name="currency").one()
    fv.extracted_value = "US$"
    db_session.commit()
    values, rows = _values(db_session, job)
    assert er.check(db_session, job, g, values, rows) == []
    book = build_for_job(er.effective_config(g), values, rows, tmp_path)
    assert openpyxl.load_workbook(book)["GENERAL"]["C2"].value == "USD"
    fv.extracted_value = "Dollars"            # ambiguous - left for a person
    db_session.commit()
    values, rows = _values(db_session, job)
    probs = er.check(db_session, job, g, values, rows)
    assert [(p["column"], p["rule"][:19]) for p in probs] == [("Currency", "A 3-letter currency")]


def test_rejection_becomes_a_suggestion(client, db_session):
    _t, g, job = _job(db_session, coo=("CN", "CN"))
    help_ = {"erp_rows": [
        {"sheet": "ITEMS", "column": "Desc", "rule": "Entry should be of 40 characters"},
        {"sheet": "GENERAL", "column": "OriginName", "rule": "This field is mandatory"},
        {"sheet": "ITEMS", "column": "Country_of_Origin", "rule": "Entry should be of 2 characters"},  # already covered
        {"sheet": "GENERAL", "column": "Currency", "rule": "The selected currency is not available"},
    ]}
    assert er.record_suggestions(db_session, job, help_) == 3
    db_session.commit()
    sa = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@example.com")
    assert login(client, sa.email).status_code == 200
    sugs = {s["key"]: s for s in client.get(f"/api/v1/erp-rules/{g.id}").json()["suggestions"]}
    assert set(sugs) == {"ITEMS|Desc", "GENERAL|OriginName", "GENERAL|Currency"}
    assert sugs["ITEMS|Desc"]["rule_text"] == "Exactly 40 characters." and sugs["ITEMS|Desc"]["last_job"] == job.reference
    assert client.post(f"/api/v1/erp-rules/{g.id}/suggestions/accept", json={"key": "ITEMS|Desc"}).json()["rule"] == {"length": 40, "length_mode": "exact"}
    assert client.post(f"/api/v1/erp-rules/{g.id}/suggestions/accept", json={"key": "GENERAL|Currency"}).status_code == 422
    assert client.post(f"/api/v1/erp-rules/{g.id}/suggestions/dismiss", json={"key": "GENERAL|OriginName"}).status_code == 200
    # saving rules keeps the remaining suggestion; a dismissed one is not raised again
    client.put(f"/api/v1/erp-rules/{g.id}", json={"rules": {"ITEMS|Desc": {"length": 40, "length_mode": "exact"}}})
    db_session.expire_all()
    stored = db_session.get(TemplateGroup, g.id).erp_column_rules
    assert set(stored["_suggested"]) == {"GENERAL|Currency"}
    assert er.record_suggestions(db_session, job, {"erp_rows": [help_["erp_rows"][1]]}) == 0


def test_live_check_endpoint(client, db_session):
    from app.models.user import TENANT_ADMIN

    t, _g, job = _job(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=t, email=f"ta-{uuid.uuid4().hex[:6]}@example.com")
    sa = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@example.com")
    assert login(client, sa.email).status_code == 200
    body = client.get(f"/api/v1/jobs/{job.id}/erp-check").json()
    assert body["checked"] and body["items"][0]["sent"] == ["Polska"] and body["items"][0]["field_value_ids"]


# ---------------------------------------------------------------- dropdown boxes save the code

def test_boxes_feeding_code_columns_and_saving_the_code(client, db_session):
    from app.models.user import OPERATOR, TENANT_ADMIN

    assert er.master_by_label(_job(db_session)[1]) == {"Origin": "country", "CountryOfOriginCode": "country",
                                                      "currency": "currency"}
    t, _g, job = _job(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=t, email=f"ta-{uuid.uuid4().hex[:6]}@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=t, email=f"op-{uuid.uuid4().hex[:6]}@example.com")
    assert login(client, op.email).status_code == 200
    detail = client.get(f"/api/v1/jobs/{job.id}").json()
    origin = next(f for f in detail["field_values"] if f["label_name"] == "Origin")
    assert origin["erp_master"] == "country"
    r = client.patch(f"/api/v1/job-field-values/{origin['id']}", json={"corrected_value": "Poland"})
    assert r.status_code == 200 and r.json()["corrected_value"] == "PL" and r.json()["erp_master"] == "country"
    r = client.patch(f"/api/v1/job-field-values/{origin['id']}", json={"corrected_value": "POLLNAD"})
    assert r.status_code == 422 and "Pick from the list" in r.json()["detail"]
    countries = client.get("/api/v1/erp-rules/masters/country").json()
    assert {"code": "PL", "name": "Poland"} in countries
    assert client.get("/api/v1/erp-rules/masters/currency").json()[0] == {"code": "INR", "name": "Indian Rupee"}


def test_ai_aided_box_keeps_its_own_conversion(db_session):
    """A box with Mageshwar's AI-aided condition keeps it (no dropdown, no code check on save);
    "BRAZIL (BR)" still reads as BR anywhere a code is needed."""
    from app.models.custom_field import CustomField

    assert normalize_country("BRAZIL (BR)") == "BR"
    t, g, _j = _job(db_session)
    db_session.add(CustomField(tenant_id=t.id, group_id=g.id, label_name="Origin", kind="ai",
                               ai_aided_prompt="COUNTRY (ISO2)"))
    db_session.commit()
    assert "Origin" not in er.master_by_label(g)
    assert er.master_by_label(g)["CountryOfOriginCode"] == "country"

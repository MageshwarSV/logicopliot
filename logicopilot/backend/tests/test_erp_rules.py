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

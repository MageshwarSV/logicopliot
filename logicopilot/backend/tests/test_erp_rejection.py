"""ERP import rejection -> what the operator fixes (erp_rejection.py)."""
import json
from types import SimpleNamespace

from app.core import erp_rejection as er
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

# As SOFTLINK's dialog reads through innerText (JOB-E6C19F, 2026-11-26).
SCREEN = (
    "Invalid Data Found.\n"
    "Sheet\tField\tDescription\n"
    "ITEMS\tCountry_of_Origin\tInvoice No. #1 Product No. #1 Entry should be of 2 characters\n"
    "ITEMS\tCOO_Issuing_Country\tInvoice No. #1 Product No. #1 Entry should be of 2 characters\n"
    "ITEMS\tCountry_of_Origin\tInvoice No. #1 Product No. #2 Entry should be of 2 characters\n"
    "ITEMS\tCOO_Issuing_Country\tInvoice No. #1 Product No. #2 Entry should be of 2 characters\n"
    "ITEMS\tUQC\tInvoice No. #1 Product No. #1 Invalid unit\n"
    "Close\tDownload Error File\n"
)

CFG = {"sheets": [{"sheet": "ITEMS", "scope": "line", "columns": [
    {"header": "ItemSrNo", "field": "#row"},
    {"header": "Desc", "field": "product_description"},
    {"header": "Country_of_Origin", "field": "country_of_origin"},
    {"header": "COO_Issuing_Country", "field": "country_of_origin"},
    {"header": "UQC", "value": "NOS"},
]}]}


def test_parse_reads_every_row():
    rows = er.parse_rows(SCREEN)
    assert len(rows) == 5
    assert rows[0] == {"sheet": "ITEMS", "column": "Country_of_Origin", "invoice": 1, "product": 1,
                       "rule": "Entry should be of 2 characters",
                       "text": "ITEMS Country_of_Origin Invoice No. #1 Product No. #1 Entry should be of 2 characters"}
    assert rows[2]["product"] == 2


def test_parse_without_tabs_and_nothing_readable():
    loose = SCREEN.replace("\t", " ")
    assert [r["column"] for r in er.parse_rows(loose)][:2] == ["Country_of_Origin", "COO_Issuing_Country"]
    assert er.parse_rows("") == [] and er.parse_rows("Invalid Data Found.\nClose") == []


def _job(db):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved",
                      entry_mode="excel", excel_config=CFG)
    db.add(g)
    db.flush()
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-R1", status="extracted")
    db.add(job)
    db.flush()
    for row, desc in ((1, "Router"), (2, "Cable")):
        db.add_all([
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="product_description", row_index=row, extracted_value=desc),
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="country_of_origin", row_index=row, extracted_value="China"),
        ])
    db.commit()
    return job


def test_items_merge_two_erp_columns_and_both_products(db_session):
    job = _job(db_session)
    items = er.build_items(db_session, job, er.parse_rows(SCREEN))
    assert len(items) == 2
    coo = items[0]
    assert coo["field"] == "country_of_origin" and coo["display"] == "Country of origin"
    assert coo["erp_columns"] == ["Country_of_Origin", "COO_Issuing_Country"]
    assert coo["where"] == "Invoice 1 · Products 1, 2"
    assert coo["sent"] == ["China"] and coo["fixable"] is True
    ids = {f.id for f in db_session.query(JobFieldValue).filter_by(label_name="country_of_origin")}
    assert set(coo["field_value_ids"]) == ids
    # a fixed value in the Excel setup: the operator cannot change it
    uqc = items[1]
    assert uqc["fixable"] is False and uqc["sent"] == ["NOS"] and "Excel setup" in uqc["support_reason"]


def test_explain_fills_plain_words(db_session, monkeypatch):
    job = _job(db_session)
    items = er.build_items(db_session, job, er.parse_rows(SCREEN))
    answer = {"items": [{"n": 1, "needs": "A 2-letter country code.", "example": "CN for China", "suggest": "CN"},
                        {"n": 2, "needs": "A unit the ERP knows.", "example": "", "suggest": ""}]}
    fake = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))])
    monkeypatch.setattr("app.core.llm.create_chat_completion_with_retry", lambda **k: fake)
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(openai_api_key="x", openai_model="m"))
    assert er.explain(items, job.tenant_id) is True
    assert items[0]["needs"] == "A 2-letter country code." and items[0]["suggest"] == "CN"


def test_without_ai_the_erp_rule_is_shown(db_session):
    job = _job(db_session)
    help_ = er.build_help(db_session, job, SCREEN, use_ai=False)
    assert help_["kind"] == "import_rejected" and help_["ai"] is False
    assert help_["items"][0]["needs"] == "Entry should be of 2 characters"
    assert len(help_["erp_rows"]) == 5 and help_["excel_edited"] is False
    assert er.build_help(db_session, job, "nothing here", use_ai=False) is None

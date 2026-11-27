"""AI-aided field (4S, 2026-11-27): a column's "ai_map" turns the value in the box into the code
the ERP expects - the field's own list first (label, other names, near spelling), the AI only
for a value the list cannot place, blank stays blank, nothing unsure ever reaches the Excel.
First use: Sea Export's Port of Loading -> CustomHouseCode / Loading_Port (ICEGATE code).
"""
import pytest
from fastapi import HTTPException

from app.core import excel_entry as xe
from app.core import llm


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    asked = []

    def ai(raw, options, instruction, pattern="", tenant_id=None):
        asked.append(raw)
        return {"ICD CONCOR TONDIARPET": "INTON6", "ICD CONCOR TONDIARPET, CHENNAI": "INTON6",
                "WRONG SHAPE": "CHENNAI"}.get(raw.upper(), "")

    monkeypatch.setattr(llm, "ai_map_with_ai", ai)
    xe._AI_MAP_CACHE.clear()
    return asked


@pytest.mark.parametrize(("box", "code"), [
    ("Chennai", "INMAA1"), ("CHENNAI", "INMAA1"), ("Madras", "INMAA1"), ("CHENNAI SEA PORT, INDIA", "INMAA1"),
    ("Kattupalli", "INKAT1"), ("Ennore", "INENR1"), ("Tuticorin", "INTUT1"), ("Thoothukudi", "INTUT1"),
    ("Cochin", "INCOK1"), ("Vizag", "INVTZ1"), ("Kolkata", "INCCU1"), ("Nhava Sheva", "INNSA1"),
    ("JNPT", "INNSA1"), ("Mumbai", "INBOM1"), ("Mundra", "INMUN1"), ("Kandla", "INIXY1"),
    ("Goa", "INMRM1"), ("ICD Whitefield", "INWFD6"),
])
def test_every_listed_port(box, code, fake_ai):
    assert xe._sea_port_code(box) == code
    assert fake_ai == []                       # no AI call for a listed port


def test_a_near_spelling_takes_the_nearest_port(fake_ai):
    assert xe._sea_port_code("katupilli") == "INKAT1"
    assert xe._sea_port_code("Tutucorin") == "INTUT1"
    assert fake_ai == []


def test_a_code_typed_as_a_code_is_kept():
    assert xe._sea_port_code("inmaa1") == "INMAA1"


def test_blank_stays_blank(fake_ai):
    assert xe._sea_port_code("") == "" and xe._sea_port_code("   ") == ""
    assert fake_ai == []


def test_a_value_not_on_the_list_is_asked_of_the_ai_once(fake_ai):
    assert xe._sea_port_code("ICD CONCOR Tondiarpet") == "INTON6"
    assert xe._sea_port_code("ICD CONCOR Tondiarpet") == "INTON6"
    assert fake_ai == ["ICD CONCOR Tondiarpet"]


def test_an_icd_named_after_a_city_is_not_taken_for_the_city_port(fake_ai):
    """'ICD CONCOR Tondiarpet, Chennai' is an ICD, not Chennai sea port - it goes to the AI."""
    assert xe._sea_port_code("ICD CONCOR Tondiarpet, Chennai") == "INTON6"


def test_unknown_place_or_badly_shaped_answer_gives_nothing():
    assert xe._sea_port_code("Somewhere Unknown") == ""
    assert xe._sea_port_code("wrong shape") == ""          # AI said 'CHENNAI' - not a code


def test_a_column_ai_map_works_for_any_list(fake_ai):
    spec = {"options": [{"label": "Transaction", "code": "T"}, {"label": "Deferred", "code": "D"}],
            "instruction": "duty payment method"}
    cfg = {"sheets": [{"sheet": "GENERAL", "scope": "job", "columns": [
        {"column": "A", "header": "DutyPay", "field": "Payment Method", "ai_map": spec},
        {"column": "B", "header": "Loading_Port", "field": "PORT OF LOADING", "ai_map": xe.SEA_PORT_MAP},
    ]}]}
    plan = xe.sheet_plans(cfg)[0]
    assert xe._rows_for_plan(plan, {"Payment Method": "transaction", "PORT OF LOADING": "Kattupalli"}, {}) \
        == [["T", "INKAT1"]]


def test_the_api_checks_the_setting():
    from app.api.v1.onboarding_template import _clean_ai_map

    ok = _clean_ai_map({"options": [{"label": "Chennai", "code": "INMAA1", "aliases": ["Madras", ""]},
                                    {"label": "", "code": ""}],
                        "instruction": " sea port ", "pattern": "IN[A-Z]{3}[0-9A-Z]", "dropdown": True})
    assert ok == {"options": [{"label": "Chennai", "code": "INMAA1", "aliases": ["Madras"]}],
                  "instruction": "sea port", "pattern": "IN[A-Z]{3}[0-9A-Z]", "dropdown": True}
    with pytest.raises(HTTPException):
        _clean_ai_map({"options": []})
    with pytest.raises(HTTPException):                     # a code that breaks its own format
        _clean_ai_map({"options": [{"label": "Chennai", "code": "MAA"}], "pattern": "IN[A-Z]{3}[0-9A-Z]"})


def test_the_dropdown_text_with_its_code_gives_that_code(fake_ai):
    """The box saves "Ennore (INENR1)" - the code inside is the answer, no AI."""
    assert xe._sea_port_code("Ennore (INENR1)") == "INENR1"
    assert xe._sea_port_code("ICD Whitefield (INWFD6)") == "INWFD6"
    assert fake_ai == []

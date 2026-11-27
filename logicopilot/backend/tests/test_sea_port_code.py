"""Sea Export Port of Loading -> ICEGATE code for CustomHouseCode / Loading_Port (4S, 2026-11-27).

The Excel follows the Port of Loading box itself, so an operator's correction reaches it: a
listed port gives its code, a near spelling the nearest port's, anything else is asked of the AI
once (an ICD not on the list - "ICD CONCOR"), blank stays blank.
"""
import pytest

from app.core import excel_entry as xe
from app.core import llm


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    asked = []

    def ai(port, tenant_id=None):
        asked.append(port)
        return {"ICD CONCOR TONDIARPET": "INTON6"}.get(port.upper(), "")

    monkeypatch.setattr(llm, "sea_port_code_with_ai", ai)
    xe._PORT_AI_CACHE.clear()
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


def test_a_port_not_on_the_list_is_asked_of_the_ai_once(fake_ai):
    assert xe._sea_port_code("ICD CONCOR Tondiarpet") == "INTON6"
    assert xe._sea_port_code("ICD CONCOR Tondiarpet") == "INTON6"
    assert fake_ai == ["ICD CONCOR Tondiarpet"]


def test_unknown_place_gives_nothing_rather_than_a_guess():
    assert xe._sea_port_code("Somewhere Unknown") == ""


def test_the_column_transform_uses_it():
    cfg = {"sheets": [{"sheet": "GENERAL", "scope": "job", "columns": [
        {"column": "B", "header": "CustomHouseCode", "field": "PORT OF LOADING", "transform": "sea_port_code"},
        {"column": "E", "header": "Loading_Port", "field": "PORT OF LOADING", "transform": "sea_port_code"},
    ]}]}
    plan = xe.sheet_plans(cfg)[0]
    assert xe._rows_for_plan(plan, {"PORT OF LOADING": "Kattupalli"}, {}) == [["INKAT1", "INKAT1"]]

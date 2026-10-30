"""A column that names BOTH a field and a literal reads them as a CHAIN, not a choice: its
own field first, then each of `fallback_fields` in turn, and the literal only as a last resort.

Built for Ins_Currency. That column was mapped {"field": "", "value": "INR"} - a bare literal -
so every customs entry this system has ever produced declared the insurance in rupees no matter
what the invoice said. It now reads the operator's own answer, falls back to the freight
currency already read off the document, and only then to the "INR" that was being written
anyway. The chain's last link IS today's behaviour, which is what makes the change safe: a job
with nothing new filled in still exports exactly what it exported before.
"""
from app.core.excel_entry import _rows_for_plan

CHAINED = {
    "scope": "job",
    "header_row": 1,
    "columns": [{
        "column": "N", "header": "Ins_Currency", "field": "Ins_Currency",
        "fallback_fields": ["Frt_Currency"], "value": "INR",
    }],
}


def _one(values, rows=None):
    data = _rows_for_plan(CHAINED, values=values, rows=rows or {})
    assert len(data) == 1
    return data[0][0]


def test_the_fields_own_value_wins():
    assert _one({"Ins_Currency": "EUR", "Frt_Currency": "USD"}) == "EUR"


def test_it_falls_back_to_the_next_field_when_its_own_is_blank():
    """Nobody touched the insurance currency, but the invoice's freight currency was read -
    that is the answer, not rupees."""
    assert _one({"Ins_Currency": "", "Frt_Currency": "USD"}) == "USD"


def test_it_falls_back_to_the_next_field_when_its_own_is_missing_entirely():
    """A job extracted before the Ins_Currency field existed has no value for it at all."""
    assert _one({"Frt_Currency": "USD"}) == "USD"


def test_whitespace_does_not_count_as_an_answer():
    assert _one({"Ins_Currency": "   ", "Frt_Currency": "USD"}) == "USD"


def test_the_literal_is_the_last_resort_not_the_first():
    """Both fields blank - this is the pre-existing behaviour, unchanged, and is why nothing
    that exports correctly today can start exporting worse."""
    assert _one({"Ins_Currency": "", "Frt_Currency": ""}) == "INR"
    assert _one({}) == "INR"


def test_a_literal_only_column_is_completely_untouched():
    """Every other literal column in every template has no `field`, so it never enters the
    chain at all - it still writes its literal regardless of what any field holds."""
    plan = {"scope": "job", "header_row": 1,
            "columns": [{"column": "H", "header": "Is_Single", "field": "", "value": "Y"}]}
    assert _rows_for_plan(plan, values={"Is_Single": "N"}, rows={}) == [["Y"]]


def test_a_field_only_column_is_completely_untouched():
    plan = {"scope": "job", "header_row": 1,
            "columns": [{"column": "K", "header": "Frt_Currency", "field": "Frt_Currency"}]}
    assert _rows_for_plan(plan, values={"Frt_Currency": "USD"}, rows={}) == [["USD"]]
    assert _rows_for_plan(plan, values={}, rows={}) == [[""]]


def test_the_chain_works_per_line_on_a_line_scoped_sheet():
    """Each row resolves its own link independently - line 2 falling back must not drag line
    1's answer along with it."""
    plan = {
        "scope": "line", "header_row": 1,
        "columns": [{"column": "A", "field": "per_line", "fallback_fields": ["backup"],
                     "value": "LAST"}],
    }
    data = _rows_for_plan(
        plan,
        values={"backup": "FROM-JOB"},
        rows={"per_line": ["OWN", "", ""]},
    )
    assert data == [["OWN"], ["FROM-JOB"], ["FROM-JOB"]]


def test_fallback_fields_are_tried_in_order():
    plan = {"scope": "job", "header_row": 1,
            "columns": [{"column": "A", "field": "first", "fallback_fields": ["second", "third"],
                         "value": "LAST"}]}
    assert _rows_for_plan(plan, values={"second": "B", "third": "C"}, rows={}) == [["B"]]
    assert _rows_for_plan(plan, values={"third": "C"}, rows={}) == [["C"]]
    assert _rows_for_plan(plan, values={}, rows={}) == [["LAST"]]

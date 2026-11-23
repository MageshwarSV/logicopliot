"""P6 money checks: the invoice's own numbers must add up; a failure is a finding, never a change."""
from types import SimpleNamespace

from app.core import money_checks as mc


def _line(q, p, a):
    return {mc.QTY: q, mc.PRICE: p, mc.AMOUNT: a}


def test_job_5b0b6d_adds_up_in_standard_and_european_form():
    assert mc.check_values({(1, 1): _line("1450", "2.83", "4103.50")}, {1: ["1450"]}, ["4103.50"]) == []
    assert mc.check_values({(1, 1): _line("1.450", "2,83", "4.103,50")}, {1: ["1.450"]}, ["4.103,50"]) == []


def test_line_that_does_not_add_up():
    out = mc.check_values({(1, 3): _line("10", "5.00", "60.00")}, {}, [])
    assert len(out) == 1 and out[0]["status"] == "mismatch" and "line 3" in out[0]["label"]
    assert "50.00" in out[0]["source_value"]


def test_sum_of_lines_against_product_value():
    lines = {(1, 1): _line("2", "10", "20"), (1, 2): _line("1", "5", "5")}
    assert mc.check_values(lines, {}, ["25.00"]) == []
    out = mc.check_values(lines, {}, ["250.00"])
    assert [f["label"] for f in out] == ["Sum of invoice line amounts = Product Value"]
    assert "25.00" in out[0]["source_value"]


def test_freight_like_total_slip_is_caught():
    # the run where the total came back as a different number
    out = mc.check_values({(1, 1): _line("1450", "2.83", "4103.50")}, {}, ["41035.00"])
    assert len(out) == 1


def test_quantity_total_per_invoice():
    lines = {(1, 1): _line("1000", "1", "1000"), (1, 2): _line("450", "1", "450"),
             (2, 1): _line("7", "1", "7")}
    assert mc.check_values(lines, {1: ["1450"], 2: ["7"]}, []) == []
    out = mc.check_values(lines, {1: ["1450"], 2: ["9"]}, [])
    assert len(out) == 1 and "invoice 2" in out[0]["label"]


def test_missing_or_unreadable_values_are_skipped_not_flagged():
    assert mc.check_values({(1, 1): _line("", "2.83", "4103.50")}, {1: ["N/A"]}, ["see attached"]) == []


def test_finding_id_changes_when_a_value_changes():
    a = mc.check_values({(1, 1): _line("10", "5", "60")}, {}, [])[0]["id"]
    b = mc.check_values({(1, 1): _line("10", "5", "70")}, {}, [])[0]["id"]
    assert a != b and a.startswith("money:line:")


def test_off_by_default_returns_nothing(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace())
    assert mc.findings(db=None, job=None) == []

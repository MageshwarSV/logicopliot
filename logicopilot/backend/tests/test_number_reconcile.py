"""European number format on invoices (JOB-5B0B6D): '1.450 PC  2,83  4.103,50' = 1450 x 2.83 =
4103.50 - the same model returned it as printed in one run and converted in the next."""
from app.core.extraction import number_readings, reconcile_row_numbers, reconcile_total

ROW = ("item_quantity", "item_unit_price", "product_amount")


def _settle(q, p, a):
    row = dict(zip(ROW, (q, p, a)))
    reconcile_row_numbers([row])
    return tuple(row[k] for k in ROW)


def test_european_as_printed_is_converted():
    assert _settle("1.450", "2,83", "4.103,50") == ("1450", "2.83", "4103.50")
    assert _settle("1.450", "2,83", "4103,50") == ("1450", "2.83", "4103.50")


def test_half_converted_reading_is_settled_by_the_arithmetic():
    assert _settle("1.450", "2.83", "4103.50") == ("1450", "2.83", "4103.50")
    assert _settle("1,450", "2.83", "4,103.50") == ("1450", "2.83", "4103.50")


def test_already_standard_and_real_small_quantities_are_left_alone():
    assert _settle("1450", "2.83", "4103.50") == ("1450", "2.83", "4103.50")
    assert _settle("1.450", "2.83", "4.10") == ("1.450", "2.83", "4.10")
    assert _settle("30000", "0.012000", "360.00") == ("30000", "0.012000", "360.00")


def test_codes_and_text_are_never_numbers():
    assert number_readings("8471.30.00") == []
    assert number_readings("ABC12") == []


def test_total_settled_by_the_lines_sum():
    assert reconcile_total("1.450", 1450.0) == "1450"
    assert reconcile_total("1.450", 1.45) == "1.450"
    assert reconcile_total("4.103,50", None) == "4103.50"
    assert reconcile_total("1.450", None) is None

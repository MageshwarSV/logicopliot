"""compare_values: date-aware comparison (a date written two different ways is not a
disagreement), numeric_total (comparing a whole-shipment summary against an itemized
breakdown by total instead of demanding equal row counts), and party=True (a company
name/address field scored by containment, not the usual word-overlap Jaccard, so extra
legitimate detail on one side doesn't count against the match)."""
from app.core.extraction import compare_values, is_party_field, numeric_total


def test_same_date_written_dd_mon_yyyy_vs_iso_is_a_match():
    assert compare_values("12-Aug-2026", "2026-08-12") == "match"


def test_same_date_written_slash_vs_dd_mon_yyyy_is_a_match():
    assert compare_values("08/09/2026", "08-Sep-2026") == "match"


def test_same_date_two_digit_year_is_a_match():
    assert compare_values("15-SEP-26", "2026-09-15") == "match"


def test_genuinely_different_dates_are_still_a_mismatch():
    assert compare_values("16.06.2026", "04/17/2026") == "mismatch"


def test_a_plain_number_is_not_mistaken_for_a_date():
    assert compare_values("13674", "13674") == "match"
    assert compare_values("13674", "1725.00") == "mismatch"


def test_month_day_year_used_only_when_day_first_cannot_parse():
    # 17 cannot be a month, so day-first parsing fails and month/day/year is tried instead.
    assert compare_values("04/17/2026", "2026-04-17") == "match"


# ---- negative numbers ---------------------------------------------------------------------
# _normalize strips ALL punctuation, including a leading minus sign - "-50" and "50" used to
# both normalize to "50" and match at the very first equality check, before the strict
# numeric comparison further down ever ran. A credit-note/adjustment figure's sign was
# silently lost.

def test_a_negative_number_is_not_mistaken_for_its_positive_counterpart():
    assert compare_values("-50", "50") == "mismatch"
    assert compare_values("50", "-50") == "mismatch"


def test_two_equal_negative_numbers_still_match():
    assert compare_values("-50", "-50") == "match"


def test_two_different_negative_numbers_are_still_a_mismatch():
    assert compare_values("-50", "-60") == "mismatch"


def test_a_hyphenated_identifier_is_unaffected_by_the_negative_number_fix():
    # The hyphens here are separators, not a sign - must not be reinterpreted as one.
    assert compare_values("INV-2026-001", "INV-2026-001") == "match"


def test_numeric_total_keeps_a_negative_sign():
    assert numeric_total(["-50", "50"]) == 0.0
    assert numeric_total(["-50", "-1,500.00"]) == -1550.0


# ---- numeric_total -----------------------------------------------------------------------

def test_numeric_total_sums_a_plain_numeric_column():
    assert numeric_total(["100", "200", "300"]) == 600


def test_numeric_total_reads_the_first_number_out_of_a_unit_suffixed_value():
    assert numeric_total(["510.25 Kg"]) == 510.25


def test_numeric_total_is_none_if_any_value_is_text():
    assert numeric_total(["100", "PE MULTILAYER PLASTIC FILM"]) is None


def test_numeric_total_is_none_for_an_empty_column():
    assert numeric_total([]) is None


# ---- is_party_field ------------------------------------------------------------------------

def test_is_party_field_recognises_common_party_labels():
    for label in ("CONSIGNEE", "EXPORTER", "Supplier Name", "Supplier Address",
                  "Notify Party", "Buyer", "Manufacturer"):
        assert is_party_field(label), label


def test_is_party_field_does_not_flag_ordinary_fields():
    for label in ("product_description", "item_quantity", "Gross Wt", "HBL No",
                  "Invoice Date", "Customer Part code"):
        assert not is_party_field(label), label


# ---- compare_values(party=True) --------------------------------------------------------------

def test_party_match_tolerates_one_side_adding_legitimate_extra_detail():
    """The real case this was built for: the packing list adds a plot number and a state the
    invoice doesn't print, for the same company at the same place."""
    invoice = ("ANNORA PHARMA PRIVATE LIMITED SY.NO. 261,ANNARAM VILLAGE,,GUMMADIDALA MANDAL,"
              "SANGAREDDY DISTRICT, HYDERABAD,502313, TELANGANA,India.")
    packing_list = ("ANNORA PHARMA PRIVATE LIMITED.,SY.NO.261,PLOT NO.13 TO 14,ANNARAM VILLAGE,"
                    "GUMMADIDAL MANDAL,HYDERABAD SANGAREDDY TELANGANA-502313 INDIA")
    assert compare_values(invoice, packing_list, party=False) == "review"  # unchanged default
    assert compare_values(invoice, packing_list, party=True) == "match"


def test_party_mode_does_not_paper_over_a_different_company():
    assert compare_values("BRAKES INDIA PRIVATE LIMITED, ANDHRA PRADESH",
                          "SCANIA LOGISTIC CENTER, NETHERLANDS", party=True) == "mismatch"


def test_a_changed_dosage_number_still_needs_review_even_in_party_mode():
    """party=True must never actually be PASSED for a description/dosage field - is_party_field
    is what keeps it gated to company name/address fields at the call site. This just confirms
    the changed token ('35mg' vs '70mg') still keeps the score out of the match band either
    way, so party mode is not itself a silent way to launder a wrong dosage through."""
    a, b = "CITALOPRAM SODIUM TABLETS USP 35mg", "CITALOPRAM SODIUM TABLETS 70MG"
    assert compare_values(a, b, party=False) == "review"
    assert compare_values(a, b, party=True) == "review"

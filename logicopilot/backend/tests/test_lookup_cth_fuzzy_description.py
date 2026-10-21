"""lookup_cth's description fallback used to require an exact match (after normalising case
and spacing) - found live: the document's own wording for a product is often a shorter or
differently-worded version of what the customer's master lists for the identical product
("GASKET SET" on the invoice vs "GASKET SET - ENGINE ASSY TYPE 2" in the master), so a
genuine match was silently coming back blank just because the word counts didn't line up.

Confirmed with the user: loosen the DESCRIPTION match only (never the part code) to count at
or above 80% textual similarity - see _DESC_FUZZY_THRESHOLD."""
from app.core.material_master import lookup_cth


def test_a_shorter_description_than_the_masters_own_still_matches():
    master = {"HYDRAULIC HOSE ASSY": "84842000"}
    assert lookup_cth("", master, description="HYDRAULIC HOSE") == "84842000"


def test_a_longer_description_than_the_masters_own_still_matches():
    master = {"AREC PA PLATE": "73181500"}
    assert lookup_cth("", master, description="AREC PA PLATE A") == "73181500"


def test_an_exact_description_match_is_unaffected():
    master = {"WIDGET ONE": "11111111", "SOMETHING ELSE ENTIRELY": "22222222"}
    assert lookup_cth("", master, description="Widget One") == "11111111"


def test_two_different_products_that_tie_above_threshold_stay_ambiguous():
    # Two genuinely different real products whose descriptions happen to read similarly -
    # must NOT silently pick one. This is the whole reason exact matching was strict to begin
    # with; the fuzzy fallback must not reintroduce that exact risk.
    master = {
        "STEEL BRACKET TYPE A": "11112222",
        "STEEL BRACKET TYPE B": "33334444",
    }
    assert lookup_cth("", master, description="STEEL BRACKET TYPE") == ""


def test_a_part_code_is_never_fuzzy_matched_against_a_description():
    # A master key with no space is treated as a part code, never a candidate for the
    # description fuzzy pass - a code must stay an exact match, always.
    master = {"839599A": "85177990"}
    assert lookup_cth("", master, description="839599") == ""


def test_below_the_similarity_threshold_still_returns_nothing():
    master = {"COMPLETELY UNRELATED PRODUCT NAME": "99999999"}
    assert lookup_cth("", master, description="SOMETHING ELSE ENTIRELY DIFFERENT") == ""


def test_an_exact_part_code_match_is_tried_before_any_description_fuzzing():
    master = {"839599A": "85177990", "SIMILAR PRODUCT NAME": "00000000"}
    assert lookup_cth("839599A", master, description="SIMILAR PRODUCT") == "85177990"

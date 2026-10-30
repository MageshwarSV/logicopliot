"""lookup_cth_with_source - WHICH key of the customer's sheet produced a CTH.

An operator checking a code needs to know that: matched on the part number it is the precise
answer, matched only on the wording it is worth a second look - and on these invoices the part
code is frequently blank, so the description was the only thing that could have matched at all.

The code itself must be byte-for-byte what lookup_cth already returned; this only adds the
second half of the answer. Every existing lookup_cth test still passing is the other half of
that guarantee.
"""
from app.core.material_master import (
    CTH_SOURCE_DESCRIPTION,
    CTH_SOURCE_PART_CODE,
    lookup_cth,
    lookup_cth_with_source,
)


def test_a_part_code_hit_is_reported_as_the_part_code():
    master = {"BB00003875-LH": "87089900"}
    assert lookup_cth_with_source("BB00003875-LH", master) == ("87089900", CTH_SOURCE_PART_CODE)


def test_a_description_hit_is_reported_as_the_description():
    master = {"HYDRAULIC HOSE ASSY": "84842000"}
    code, source = lookup_cth_with_source("", master, description="HYDRAULIC HOSE ASSY")
    assert (code, source) == ("84842000", CTH_SOURCE_DESCRIPTION)


def test_the_part_code_wins_when_BOTH_could_match():
    """The order lookup_cth has always used - the part number is the more precise thing to
    match on. Worth pinning here too, because the reported source is now visible to the
    operator and would be actively misleading if it named the key that did NOT decide it."""
    master = {"PART-1": "11111111", "SOME WIDGET": "22222222"}
    assert lookup_cth_with_source("PART-1", master, description="SOME WIDGET") == (
        "11111111", CTH_SOURCE_PART_CODE,
    )


def test_a_fuzzy_description_fallback_is_still_the_description():
    """The 80%-similarity fallback (see test_lookup_cth_fuzzy_description) is reached only
    after both exact passes fail - it is still the wording that answered, not the code."""
    master = {"HYDRAULIC HOSE ASSY": "84842000"}
    code, source = lookup_cth_with_source("", master, description="HYDRAULIC HOSE")
    assert (code, source) == ("84842000", CTH_SOURCE_DESCRIPTION)


def test_a_variant_suffix_hit_still_names_the_key_that_matched():
    """094624A on the invoice against 094624A.101 in the master - matched on the stem of the
    PART CODE, so that is what it must say."""
    master = {"094624A.101": "73181500", "094624A.P01": ""}
    assert lookup_cth_with_source("094624A", master) == ("73181500", CTH_SOURCE_PART_CODE)


def test_no_match_reports_no_source():
    """("", "") - never a source for a code that was not found, since the screen shows the
    source only to explain a value that IS there."""
    assert lookup_cth_with_source("NOTHING", {"SOMETHING": "11111111"}) == ("", "")
    assert lookup_cth_with_source("ANY", {}) == ("", "")


def test_lookup_cth_returns_exactly_the_same_code_as_before():
    """The old one-value function is now a thin wrapper. Same inputs, same code, every path -
    a refactor that quietly changed a CTH would change a customs declaration."""
    master = {"PART-1": "11111111", "SOME WIDGET": "22222222", "094624A.101": "33333333"}
    for material, desc in [
        ("PART-1", ""), ("", "SOME WIDGET"), ("PART-1", "SOME WIDGET"),
        ("094624A", ""), ("NOTHING", "NOTHING EITHER"), ("", ""),
    ]:
        assert lookup_cth(material, master, description=desc) == (
            lookup_cth_with_source(material, master, description=desc)[0]
        ), f"diverged for {material!r}/{desc!r}"

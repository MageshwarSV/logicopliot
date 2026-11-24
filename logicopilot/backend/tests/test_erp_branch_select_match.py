"""A branch that is spelled out in full must not be answered with a shorter one.

Found on production 2026-11-24. SOFTLINK's Address dropdown lists a branch twice for
FLEXTRONICS TECHNOLOGIES (INDIA) PRIVATELIMITED - once as "KANCHIPURAM 19 (19)" and once as
plain "KANCHIPURAM", which is a different place. The job said KANCHIPURAM 19; the entry was
filed against KANCHIPURAM, and the run log said only "step 57 select ok".

_fuzzy_pick had matched both on its substring rung - one HOLDS the asked-for text, the other
is HELD BY it - and then chose between them on closeness of length alone:

    "KANCHIPURAM 19 (19)"   19 chars against 14  -> 5
    "KANCHIPURAM"           11 chars against 14  -> 3   won, and was wrong

Length is not evidence. An option that contains everything asked for keeps all of it; one the
value merely starts with throws the rest away, and the rest was the whole point.

The real dropdown is used here, exactly as the ERP lists it - including the bracketed codes,
which are why nothing matched exactly in the first place.
"""
import pytest

from app.core.browser import _fuzzy_pick

# The Address dropdown as SOFTLINK really shows it for this importer.
ERP_BRANCHES = [
    "KANCHIPURAM 19 (19)", "KUTHAMBAKKAM (21)", "ANDHRA PRADESH (24)",
    "SEZ SRIPERUMBUDUR (6)", "12 (32)", "HYDERABAD", "KATTAVAKKAM (26)",
    "TELANGANA (20)", "SRI PERUMBUDUR (19)", "8 (8)", "33 (33)", "KANCHIPURAM",
    "29 (29)", "BANGALORE PVT BOND (32)",
]


def test_the_branch_that_was_filed_wrong():
    assert _fuzzy_pick("KANCHIPURAM 19", ERP_BRANCHES) == "KANCHIPURAM 19 (19)"


def test_the_shorter_branch_still_picks_itself():
    """The other half of the pair: asking for plain KANCHIPURAM must not now drag in the 19."""
    assert _fuzzy_pick("KANCHIPURAM", ERP_BRANCHES) == "KANCHIPURAM"


@pytest.mark.parametrize(("asked", "expected"), [
    ("SRI PERUMBUDUR", "SRI PERUMBUDUR (19)"),
    ("KATTAVAKKAM", "KATTAVAKKAM (26)"),
    ("KUTHAMBAKKAM", "KUTHAMBAKKAM (21)"),
    ("ANDHRA PRADESH", "ANDHRA PRADESH (24)"),
    ("SEZ SRIPERUMBUDUR", "SEZ SRIPERUMBUDUR (6)"),
    ("TELANGANA", "TELANGANA (20)"),
    ("BANGALORE PVT BOND", "BANGALORE PVT BOND (32)"),
    ("HYDERABAD", "HYDERABAD"),
    # The code-only branches, where the bracketed number repeats the name.
    ("33", "33 (33)"),
    ("8", "8 (8)"),
    ("29", "29 (29)"),
    ("12", "12 (32)"),
])
def test_every_other_branch_on_that_dropdown_is_unchanged(asked, expected):
    assert _fuzzy_pick(asked, ERP_BRANCHES) == expected


def test_punctuation_only_difference_matches_exactly():
    """bare() swapped punctuation for a space without re-collapsing, so "SHANGHAI , CHINA"
    bared to three spaces and "SHANGHAI, CHINA" to two: the "exact ignoring punctuation" rung
    could never fire on the example its own comment gives, and the substring rung answered
    "SHANGHAI" - a country dropped."""
    assert _fuzzy_pick("SHANGHAI , CHINA", ["SHANGHAI, CHINA", "SHANGHAI", "CHINA"]) \
        == "SHANGHAI, CHINA"


def test_the_tolerances_it_is_supposed_to_have_still_hold():
    # Words in another order, from _fuzzy_pick's own comment.
    assert _fuzzy_pick("espana valencia", ["VALENCIA, ESPANA", "MADRID"]) == "VALENCIA, ESPANA"
    # Whitespace and case.
    assert _fuzzy_pick("  ultratech   cement", ["ULTRATECH CEMENT", "ULTRATECH CEMENT LTD"]) \
        == "ULTRATECH CEMENT"
    # An exact option always wins, whatever else is near it.
    assert _fuzzy_pick("Chennai Sea", ["Chennai Air Cargo", "Chennai Sea", "Chennai"]) \
        == "Chennai Sea"


def test_nothing_close_is_still_nothing():
    """It must keep saying None rather than forcing a wrong option onto the screen."""
    assert _fuzzy_pick("NOT THERE AT ALL", ["ALPHA", "BETA"]) is None

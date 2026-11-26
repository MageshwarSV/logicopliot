"""ERP dropdown matching priority (4S, 2026-11-26): exact -> the option's name / starts with the
value -> its unique bracketed code -> fuzzy. Never "contains the number somewhere".

SOFTLINK's quotation Address list for ALLISON TRANSMISSION INDIA PVT LTD reads
"ORAGADAM (3)", "3 (3)", "BRANCH # 9 (9)", "0 (0)". Asked for 3, the old substring rung matched
both 3-bearing options and "3 (3)" won only by being the shorter one.
"""
import pytest

from app.core.browser import _fuzzy_pick, _select_fuzzy, _strong_pick

QUOTATION = ["ORAGADAM (3)", "3 (3)", "BRANCH # 9 (9)", "0 (0)"]
IMPORTER = ["ORAGADAM (8)", "SEZ ORAGADAM (8)", "0 (0)", "53 (53)", "65 (65)", "83 (83)",
            "90 (90)", "KANCHIPURAM (124)"]
BRANCHES = ["CHENNAI", "TIRUPUR", "COIMBATORE", "HYDERABAD (32)", "TUTICORIN", "HARYANA",
            "MUMBAI", "CHENNAI NEW", "BANGALORE", "KOCHIN", "Punjab", "Bangalore New"]


@pytest.mark.parametrize(("asked", "options", "expected"), [
    ("3", ["3 (3)"], "3 (3)"),
    ("3", ["ORAGADAM (3)", "3 (3)"], "3 (3)"),
    ("3", QUOTATION, "3 (3)"),
    ("ORAGADAM", ["ORAGADAM (3)"], "ORAGADAM (3)"),
    ("ORAGADAM", ["ORAGADAM (3)", "3 (3)"], "ORAGADAM (3)"),
    ("BRANCH # 9", QUOTATION, "BRANCH # 9 (9)"),
    ("0", QUOTATION, "0 (0)"),
])
def test_the_required_table(asked, options, expected):
    assert _fuzzy_pick(asked, options) == expected


def test_length_no_longer_decides():
    """A long name that is the value beats a short option that merely holds it in brackets."""
    assert _fuzzy_pick("3", ["A (3)", "3 HOSUR MAIN PLANT (3)"]) == "3 HOSUR MAIN PLANT (3)"
    assert _fuzzy_pick("3", ["33 (33)", "3 (3)"]) == "3 (3)"


def test_a_number_is_a_whole_word_not_a_prefix():
    assert _strong_pick("3", ["33 (33)", "30 (30)"]) is None


def test_a_unique_code_still_finds_its_option():
    assert _fuzzy_pick("124", IMPORTER) == "KANCHIPURAM (124)"


def test_a_shared_code_picks_one_of_its_own_options():
    """4S's choice (B): a code two options share is picked silently, not refused - but one of
    THOSE two. The old substring rung answered "83 (83)", a different code."""
    assert _fuzzy_pick("8", IMPORTER) == "ORAGADAM (8)"


def test_other_production_dropdowns_unchanged():
    assert _fuzzy_pick("CHENNAI", BRANCHES) == "CHENNAI"
    assert _fuzzy_pick("CHENNAI NEW", BRANCHES) == "CHENNAI NEW"
    assert _fuzzy_pick("HYDERABAD", BRANCHES) == "HYDERABAD (32)"
    assert _fuzzy_pick("ORAGADAM", IMPORTER) == "ORAGADAM (8)"
    assert _fuzzy_pick("SEZ ORAGADAM", IMPORTER) == "SEZ ORAGADAM (8)"
    assert _fuzzy_pick("53", IMPORTER) == "53 (53)"
    assert _fuzzy_pick("KANCHIPURAM", IMPORTER) == "KANCHIPURAM (124)"
    # a bracket that is part of the name is not a code
    assert _fuzzy_pick("ABC (INDIA) PVT LTD", ["ABC (INDIA) PVT LTD", "ABC"]) == "ABC (INDIA) PVT LTD"


class FakeSelect:
    """A native <select> as Playwright sees it: label = visible text, value = hidden code."""

    def __init__(self, options):
        self.options = options          # [(label, value)]
        self.chosen = None

    def select_option(self, timeout=None, label=None, value=None):
        for lab, val in self.options:
            if (label is not None and lab == label) or (value is not None and val == value):
                self.chosen = lab
                return
        raise RuntimeError("no such option")

    def evaluate(self, _js):
        return [list(p) for p in self.options]


def test_the_hidden_code_cannot_jump_ahead_of_the_visible_name():
    """Both options store 3 behind the scenes; the browser would take the first - ORAGADAM."""
    sel = FakeSelect([("ORAGADAM (3)", "3"), ("3 (3)", "3"), ("BRANCH # 9 (9)", "9"), ("0 (0)", "0")])
    assert _select_fuzzy(sel, "3") and sel.chosen == "3 (3)"


def test_a_dropdown_that_stores_a_letter_still_takes_it():
    """'Transaction' shown, 'T' stored: the hidden value must still win over a loose text guess."""
    sel = FakeSelect([("Deferred", "D"), ("Transaction", "T")])
    assert _select_fuzzy(sel, "T") and sel.chosen == "Transaction"

"""The ERP refusing an Excel import must FAIL the job, not be clicked away.

Production, 2026-11-25. Three jobs reached the ERP, were given job numbers, and carried no
data at all: MAA/AI/35112, 35113 and 35118. All three reported `ok`.

What happened, identically on each:

    step 43  upload ok                      the Excel went in
    step 49  click FAILED (Entity tab)      the rejection dialog was covering it
    step 50  unexpected screen -> AI clicked "Close"
    ...      run continued and saved a form holding only hand-typed fields

The ERP had refused the whole import over one bad cell and said so, naming the sheet, the
column and what that column accepts. Nobody saw it: a click blocked by the dialog looks like
any other unexpected screen, so the AI takeover did what it does with a blocker - found
"Close" and pressed it. Closing it discards everything the import loaded.

Two of the three were hand-edited Excels with notification numbers typed into
IGST_LevyNotnFlag (which takes H, L or +); the third was ours, sending a 2-letter country
code where Air wants a 3-letter port code. Different causes, one ending - because the error
was thrown away.

So an import rejection now ends the run, carrying the ERP's own words. Everything else - a
session prompt, a confirmation, a notice - still goes to the AI exactly as before, which is
what these tests pin down.
"""
import pytest

import app.core.browser as browser
from app.core.browser import _import_rejection


class _Page:
    """Stands in for the Playwright page: _validation_errors is what reads it."""

    def __init__(self, *texts):
        self.texts = list(texts)


@pytest.fixture(autouse=True)
def _read_the_fake_page(monkeypatch):
    monkeypatch.setattr(browser, "_validation_errors", lambda page: page.texts)


IGST_DIALOG = """Invalid Data Found.
Sheet Field Description
ITEMS IGST_LevyNotnFlag Invoice No. #1 Product No. #1 Expected values are 'H' for Higher,'L' for Lower & '+' for plus.
ITEMS IGST_ExemptionNotnType Invoice No. #1 Product No. #1 Expected values are 'C' for Customs,'G' for GST.
ITEMS IGST_LevyNotnFlag Invoice No. #2 Product No. #2 Expected values are 'H' for Higher.
ITEMS IGST_LevyNotnFlag Invoice No. #3 Product No. #3 Expected values are 'H' for Higher."""

PORT_DIALOG = """Invalid Data Found.
Sheet Field Description
GENERAL PortOfShipmentCode Entry should be of 3 characters for transport mode Air"""


def test_the_hand_edited_igst_rejection_is_read():
    got = _import_rejection(_Page(IGST_DIALOG))
    assert got is not None
    assert "IGST_LevyNotnFlag" in got, "the operator needs the COLUMN name to fix the file"
    assert "'H' for Higher" in got, "and what that column will accept"


def test_our_own_port_code_rejection_is_read():
    got = _import_rejection(_Page(PORT_DIALOG))
    assert got is not None
    assert "PortOfShipmentCode" in got
    assert "3 characters" in got


def test_the_heading_and_table_header_are_not_the_message():
    """"Invalid Data Found." and "Sheet Field Description" say nothing an operator can act
    on - the rows underneath are the whole point."""
    got = _import_rejection(_Page(IGST_DIALOG))
    assert not got.lower().startswith("invalid data found")
    assert "Sheet Field Description" not in got


def test_a_long_rejection_is_trimmed_and_says_how_many_more():
    got = _import_rejection(_Page(IGST_DIALOG))
    assert len(got) <= 400, "this goes on a job card, not into a log file"
    assert "more)" in got, "an operator must know the list did not stop at three"


@pytest.mark.parametrize("dialog", [
    "You have logged in from another web portal,\nDo you want to kill the other session?",
    "Are you sure you want to close the form ?",
    "Update the Newly selected Importer & its branch into Customer Entity ?",
    "Selected quotation [RFQ/0004/24-25] is not valid.",
    "Job saved successfully",
    "Credit Limit for NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED has exhausted",
    "",
])
def test_every_other_dialog_still_goes_to_the_ai(dialog):
    """The narrowness IS the safety. These are all dialogs the AI handles correctly today;
    failing the run on any of them would turn a working entry into a stuck job."""
    assert _import_rejection(_Page(dialog)) is None


def test_nothing_on_screen_is_not_a_rejection():
    assert _import_rejection(_Page()) is None


def test_a_page_that_cannot_be_read_does_not_end_the_run(monkeypatch):
    """Reading the screen is a bonus. If it throws, the run carries on to the AI as before -
    never the other way round."""
    def boom(page):
        raise RuntimeError("page is gone")

    monkeypatch.setattr(browser, "_validation_errors", boom)
    assert _import_rejection(_Page("anything")) is None

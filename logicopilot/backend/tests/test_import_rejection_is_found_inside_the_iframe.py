"""The "Invalid Data Found" dialog is inside an IFRAME, and that is why it was missed.

JOB-D22706 (Air import, production 2026-11-26). The ERP refused the Excel because a CTH was
not valid, and put the reason on screen in as many words:

    Invalid Data Found.
    Sheet   Field   Description
    ITEMS   CTH     #InvSrNo=3 #ProdSrNo=8  Invalid CTH : 85366932
    ITEMS   CTH     #InvSrNo=3 #ProdSrNo=9  Invalid CTH : 85366932
    ... (12 in all)

The guard added on 2026-11-25 (_import_rejection) is meant to stop exactly this. It did not
fire. The run went to the AI instead, which pressed "Close" four times, discarding everything
the import had loaded, and the job limped on to fail later at an unrelated step with
"A field stopped accepting input and nothing on screen explained why" - a message that tells
the operator nothing, about a problem the ERP had already named precisely.

Two reasons it was missed, both fixed here:

  1. SOFTLINK renders the entry screen inside an iframe (#ifrmInfo), and `page.locator(...)`
     does not descend into a frame. Everything _validation_errors looked at was the OUTER
     document, where this dialog never is.
  2. The dialog is a plain ASP.NET panel. It has no role=dialog, no .modal, none of the
     markup _VALIDATION_SELECTORS knows - so even in the right document the selector list
     found it only by luck.

So the text is now read off the body of the page AND of every frame, by its own heading
rather than by a selector.
"""
import pytest

import app.core.browser as browser
from app.core.browser import _import_rejection, _rejection_in

# Verbatim from ai-takeover-1.png on JOB-D22706.
DIALOG = """Excel Import/Export

Invalid Data Found.
Sheet	Field	Description
ITEMS	CTH	#InvSrNo=3 #ProdSrNo=8 Invalid CTH : 85366932
ITEMS	CTH	#InvSrNo=3 #ProdSrNo=9 Invalid CTH : 85366932
ITEMS	CTH	#InvSrNo=3 #ProdSrNo=10 Invalid CTH : 85366932
ITEMS	CTH	#InvSrNo=3 #ProdSrNo=11 Invalid CTH : 85366932
ITEMS	CTH	#InvSrNo=3 #ProdSrNo=12 Invalid CTH : 85366932
Close	Download Error File"""

FORM_ONLY = """Task : New
Job Number  Transport Mode  Air  BE Type  Home
General Entity SEZ/Comm
Importer Address Branch SNo IE Code No"""


class _Locator:
    def __init__(self, text):
        self._text = text

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        if self._text is None:
            raise RuntimeError("frame detached")
        return self._text


class _Scope:
    """A page or a frame: only `locator('body').first.inner_text()` is used."""

    def __init__(self, body):
        self._body = body

    def locator(self, selector):
        assert selector == "body"
        return _Locator(self._body)


class _Page(_Scope):
    def __init__(self, body, frames=()):
        super().__init__(body)
        self.frames = list(frames)


@pytest.fixture(autouse=True)
def _no_selector_route(monkeypatch):
    """The selector list found nothing on the real job - hold it at that, so these tests
    prove the FRAME route works on its own."""
    monkeypatch.setattr(browser, "_validation_errors", lambda page: [])


def test_the_dialog_inside_the_iframe_is_found():
    """THE production case: the outer document shows only the form behind the dialog."""
    page = _Page(FORM_ONLY, frames=[_Scope(DIALOG)])
    got = _import_rejection(page)
    assert got is not None, "the operator was looking straight at this dialog"
    assert "Invalid CTH : 85366932" in got, "the bad value itself is what they must fix"
    assert "CTH" in got


def test_it_names_the_invoice_and_product_so_the_line_can_be_found():
    page = _Page(FORM_ONLY, frames=[_Scope(DIALOG)])
    got = _import_rejection(page)
    assert "#InvSrNo=3" in got
    assert "#ProdSrNo=8" in got


def test_the_heading_and_table_header_are_not_the_message():
    page = _Page(FORM_ONLY, frames=[_Scope(DIALOG)])
    got = _import_rejection(page)
    assert not got.lower().startswith("invalid data found")
    assert "Sheet\tField\tDescription" not in got


def test_all_twelve_are_not_dumped_onto_the_job_card():
    page = _Page(FORM_ONLY, frames=[_Scope(DIALOG)])
    got = _import_rejection(page)
    assert len(got) <= 400
    assert "more)" in got, "but they must be told the list did not stop at three"


def test_it_still_works_when_the_dialog_is_in_the_outer_page():
    """Not every ERP screen is framed - the non-iframe case must keep working."""
    assert _import_rejection(_Page(DIALOG)) is not None


def test_a_clean_screen_is_not_a_rejection():
    assert _import_rejection(_Page(FORM_ONLY, frames=[_Scope(FORM_ONLY)])) is None


def test_a_frame_that_disappears_mid_read_does_not_end_the_run():
    """Frames come and go while the ERP posts back. Reading one must never be the thing that
    fails a run - and a later frame still gets its turn."""
    page = _Page(FORM_ONLY, frames=[_Scope(None), _Scope(DIALOG)])
    assert _rejection_in(_Scope(None)) is None
    assert _import_rejection(page) is not None


def test_a_page_with_no_frames_attribute_is_survivable():
    page = _Scope(DIALOG)          # no .frames at all
    assert _import_rejection(page) is not None

"""The ERP's refusal must reach the operator in plain words, however it was noticed.

Cargora decides a run succeeded by asking ONE question: "did a job number appear?" The ERP
hands that number out BEFORE the data goes in, so an entry holding nothing still has one -
which is how MAA/AI/35112, 35113 and 35118 were all filed empty and reported `ok` on
2026-11-25.

The "Invalid Data Found" guard was supposed to stop that, but it only ever ran when the
dialog BLOCKED a click. A rejection that blocks nothing - the remaining steps happen to miss
it - was never looked at at all. JOB-D22706 (2026-11-26) showed the rest of that story: the
ERP had refused the Excel over an invalid CTH, and the operator was shown "A field stopped
accepting input and nothing on screen explained why".

So the screen is read once more at the END of a run that is otherwise about to be called a
success, while the browser is still open. And both places that can find a rejection - the
mid-run block and this last look - now produce the SAME message, so the job card never reads
differently depending on which one noticed.
"""
import pytest

from app.core.browser import _rejected_import_block

# Verbatim from ai-takeover-1.png on JOB-D22706.
CTH = "ITEMS CTH #InvSrNo=3 #ProdSrNo=8 Invalid CTH : 85366932 (and 9 more)"
IGST = "ITEMS IGST_LevyNotnFlag Invoice No. #1 Expected values are 'H' for Higher,'L' for Lower"


@pytest.mark.parametrize("said", [CTH, IGST])
def test_the_operator_is_told_nothing_went_in(said):
    """The single most important fact, and the one the old message buried: the entry is EMPTY.
    An operator who thinks some of it landed will go looking for a half-filled job."""
    reason = _rejected_import_block(said)["reason"]
    assert "NONE of the data went in" in reason


@pytest.mark.parametrize("said", [CTH, IGST])
def test_it_carries_the_erps_own_words(said):
    """Never a paraphrase. The ERP names the sheet, the column and the value - that is what
    the operator needs to find the line and fix it."""
    assert said in _rejected_import_block(said)["reason"]


def test_it_says_what_to_do_next():
    reason = _rejected_import_block(CTH)["reason"]
    assert "Retry" in reason
    assert "Reset to generated" in reason, "the hand-edited-Excel case needs its own way out"


def test_it_fails_the_job_rather_than_marking_it_duplicate():
    """persist_run_outcome maps 'duplicate'/'blocked'/'unknown' to status 'duplicated' and
    everything else to 'failed'. A refused import is a failure: it must show the red Failed
    badge, keep its Retry button, and save the failure screenshot."""
    assert _rejected_import_block(CTH)["outcome"] == "validation"


def test_both_places_that_find_it_say_the_same_thing():
    """The mid-run guard and the end-of-run look share this one function on purpose - the job
    card must not read differently depending on which of the two happened to notice."""
    import inspect

    import app.core.browser as browser

    body = inspect.getsource(browser._play_steps)
    # 3 since 2026-11-27: the before-every-step look (the ERP update stopped the popup from
    # blocking clicks, so the mid-run block alone no longer saw it), the mid-run block, and the
    # final-screen look - all three build the message with this one function.
    assert body.count("_rejected_import_block(") == 3, (
        "every place that finds the rejection must build the message here")


def test_the_final_look_only_runs_when_the_run_was_otherwise_a_success():
    """It must not overwrite a failure the run already has a better explanation for - a
    rejected field value, or a form that locked. Those name the actual field."""
    import inspect

    import app.core.browser as browser

    body = inspect.getsource(browser._play_steps)
    assert "if blocked is None and value_error is None:" in body
    assert "leftover = _import_rejection(page)" in body


def test_reading_the_screen_can_never_itself_fail_the_run():
    """Frames come and go as the ERP posts back, and the page may already be gone. A last look
    that throws must leave the run exactly as it was."""
    import inspect

    import app.core.browser as browser

    body = inspect.getsource(browser._play_steps)
    i = body.index("leftover = _import_rejection(page)")
    window = body[i - 200:i + 300]
    assert "try:" in window and "leftover = None" in window

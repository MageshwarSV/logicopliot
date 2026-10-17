"""_last_action_done - once the success screenshot has actually been captured, a real ERP entry
is already submitted; everything recorded after that point in the script is the Super Admin's
own double-check procedure, not part of getting the job done. A step failing there (switching
back to a list tab, a stray click) must never turn an already-successful run into a reported
failure. This is a focused unit test of the extracted decision helper in isolation, since
mocking a full Playwright browser/context/page through play_steps itself would test far more
than what actually changed."""
from app.core.browser import _last_action_done


def _steps(*actions: str) -> list[dict]:
    return [{"action": a} for a in actions]


def test_no_checkpoint_requires_the_last_element_step_to_have_run():
    """Unchanged pre-existing behavior - no success screenshot was ever reached, so every
    element step up to the very end still has to have run."""
    steps = _steps("fill", "click", "click")
    done, unfinished = _last_action_done(steps, executed={0, 1}, success_checkpoint=None)
    assert done is False
    assert unfinished == [2]


def test_no_checkpoint_and_every_step_ran_is_done():
    steps = _steps("fill", "click", "click")
    done, unfinished = _last_action_done(steps, executed={0, 1, 2}, success_checkpoint=None)
    assert done is True
    assert unfinished == []


def test_a_step_failing_after_the_checkpoint_does_not_count():
    """The exact scenario reported: the success screenshot is captured, then a later step
    (e.g. a click on a tab the ERP already closed) fails - the run must still read as done."""
    steps = _steps("click", "click", "screenshot", "switch_tab", "click")
    # Steps 0, 1 (element) and 2 (screenshot) all ran; step 4 (click, AFTER the checkpoint at
    # index 2) failed and is missing from executed.
    done, unfinished = _last_action_done(steps, executed={0, 1, 2}, success_checkpoint=2)
    assert done is True
    assert unfinished == []


def test_a_step_failing_before_the_checkpoint_still_fails_the_run():
    """The checkpoint only forgives what comes AFTER it - a real element step missing before
    the screenshot was ever taken is still a genuine failure."""
    steps = _steps("click", "click", "screenshot", "click")
    # Step 1 (element, BEFORE the checkpoint at index 2) never ran.
    done, unfinished = _last_action_done(steps, executed={0, 2}, success_checkpoint=2)
    assert done is False
    assert unfinished == [1]


def test_checkpoint_itself_is_not_an_element_step_and_never_appears_unfinished():
    steps = _steps("click", "screenshot")
    done, unfinished = _last_action_done(steps, executed={0, 1}, success_checkpoint=1)
    assert done is True
    assert unfinished == []


def test_no_element_steps_at_all_is_trivially_done():
    steps = _steps("wait", "screenshot", "switch_tab")
    done, unfinished = _last_action_done(steps, executed=set(), success_checkpoint=1)
    assert done is True
    assert unfinished == []

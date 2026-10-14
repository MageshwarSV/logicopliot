"""wait_full_load - a new ERP-script step type that waits for the PAGE ITSELF to finish
loading/settling (reusing _settle/_settle_postback), distinct from "wait" (a fixed pause)
and "wait_for"/"wait_gone" (watching one element). It has no selector/element of its own, so
it must be a CONTROL action, not an ELEMENT one - every run loop (play_steps, _replay_span,
run_next) dispatches on that split, and ELEMENT_ACTIONS falls through to the single
selector-bound _perform_step() path, which this step has nothing for."""
from app.core.browser import ALL_ACTIONS, CONTROL_ACTIONS, ELEMENT_ACTIONS


def test_wait_full_load_is_a_control_action_not_an_element_action():
    assert "wait_full_load" in CONTROL_ACTIONS
    assert "wait_full_load" not in ELEMENT_ACTIONS
    assert "wait_full_load" in ALL_ACTIONS

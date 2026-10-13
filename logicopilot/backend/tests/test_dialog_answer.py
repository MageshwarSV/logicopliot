"""_dialog_answer decides how to answer a native alert/confirm/prompt that fires during an ERP
replay. Most are armed in advance by a recorded `dialog` step; this covers what happens when
nothing was armed - the case that used to blindly accept everything, including a destructive
"close the form?" prompt that turned out to be the ERP's own rejection of an invalid Save (see
browser.py's _DESTRUCTIVE_DIALOG_WORDS for the live incident)."""
from app.core.browser import _arm_dialog, _dialog_answer


def test_unarmed_dialog_with_no_destructive_wording_defaults_to_accept():
    accept, reply = _dialog_answer(None, "You have logged in from another web portal, "
                                          "Do you want to kill the other session and continue?")
    assert accept is True
    assert reply == ""


def test_unarmed_dialog_asking_to_close_defaults_to_dismiss():
    accept, reply = _dialog_answer(None, "Are you sure you want to close the form ?")
    assert accept is False


def test_unarmed_dialog_asking_to_leave_or_discard_defaults_to_dismiss():
    assert _dialog_answer(None, "You have unsaved changes - leave anyway?")[0] is False
    assert _dialog_answer(None, "Discard your changes?")[0] is False


def test_unarmed_dialog_with_no_message_defaults_to_accept():
    accept, reply = _dialog_answer(None, "")
    assert accept is True


def test_unarmed_dialog_with_no_page_and_no_message_positional_default_still_accepts():
    # message is optional - a caller that only ever passed `page` before this change must not
    # start behaving differently just because it stopped supplying a message.
    accept, reply = _dialog_answer(None)
    assert accept is True


def test_close_confirm_right_after_a_double_click_defaults_to_accept():
    """A double_click in this replay engine only ever means "pick this row from a list-picker
    popup" - the popup confirming it wants to close right after a row was picked out is that
    pick completing, not a warning about losing work. Found live on JOB-BCE250: the ordinary
    dismiss-by-default (built for a Save the ERP silently rejected) left an Organization
    picker stuck open, and the AI's own recovery clicks wandered onto an unrelated screen."""
    accept, reply = _dialog_answer(None, "Are you sure you want to close the form ?",
                                   assume_pick_confirm=True)
    assert accept is True


def test_leave_or_discard_confirm_after_a_double_click_still_defaults_to_accept():
    assert _dialog_answer(None, "You have unsaved changes - leave anyway?",
                          assume_pick_confirm=True)[0] is True
    assert _dialog_answer(None, "Discard your changes?", assume_pick_confirm=True)[0] is True


def test_assume_pick_confirm_does_not_override_an_armed_answer():
    """A recorded `dialog` step still wins outright - assume_pick_confirm only fills in for
    an UN-ARMED dialog, same as the existing destructive-word default it's replacing. An arm
    with no recorded description (anything armed outside the normal _arm_dialog path, as
    simulated here) is trusted unconditionally - only a described arm (see the next test) gets
    compared against what actually fired."""
    from app.core.browser import _DIALOG_ANSWERS

    class _Page:
        pass

    page = _Page()
    _DIALOG_ANSWERS[page] = (False, "")
    try:
        accept, reply = _dialog_answer(page, "Are you sure you want to close the form ?",
                                       assume_pick_confirm=True)
        assert accept is False
    finally:
        del _DIALOG_ANSWERS[page]


def test_an_armed_answer_for_a_different_dialog_does_not_cover_a_destructive_one():
    """The real JOB-3DA007 incident: a credit-limit warning armed "accept" via a recorded
    `dialog` step (_arm_dialog stores the message it was recorded against alongside the
    answer). That credit-limit dialog never actually fired on this run, so the arm sat there
    unconsumed - and then a LATER, completely unrelated "are you sure you want to close the
    form ?" popup fired instead. The stale arm must not blindly accept it; the destructive-
    wording safety net still applies since the arm was never recorded against anything like
    it."""
    from app.core.browser import _DIALOG_ANSWERS

    class _Page:
        pass

    page = _Page()
    # Mirrors _arm_dialog's own [accept, reply, description] shape for a step recorded
    # against "Credit Limit for NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED has
    # exhauste[d]..." with value="accept".
    _DIALOG_ANSWERS[page] = [True, "",
                             "Credit Limit for NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE "
                             "LIMITED has exhauste"]
    try:
        accept, reply = _dialog_answer(page, "Are you sure you want to close the form ?")
        assert accept is False
    finally:
        del _DIALOG_ANSWERS[page]


def test_an_armed_answer_for_a_similarly_destructive_dialog_still_wins():
    """The recorder genuinely saw and accepted a close/discard-style dialog here - a SECOND
    one firing later (the grid-loop repeat case _arm_dialog's own docstring describes) must
    still use the recorded answer, not second-guess it."""
    from app.core.browser import _DIALOG_ANSWERS

    class _Page:
        pass

    page = _Page()
    _DIALOG_ANSWERS[page] = [True, "", "Discard unsaved changes and close this row?"]
    try:
        accept, reply = _dialog_answer(page, "Are you sure you want to close the form ?")
        assert accept is True
    finally:
        del _DIALOG_ANSWERS[page]


def test_an_armed_answer_still_wins_for_a_non_destructive_dialog():
    """The override only ever narrows what an arm can cover for - it never second-guesses an
    arm answering a dialog that was never destructive-sounding in the first place."""
    from app.core.browser import _DIALOG_ANSWERS

    class _Page:
        pass

    page = _Page()
    _DIALOG_ANSWERS[page] = [False, "", "Credit Limit for NOKIA... has exhausted"]
    try:
        accept, reply = _dialog_answer(
            page, "You have logged in from another web portal, Do you want to kill the "
                  "other session and continue?")
        assert accept is False
    finally:
        del _DIALOG_ANSWERS[page]


def test_arm_dialog_stores_the_recorded_description_for_later_comparison():
    """_arm_dialog stores the step's own `description` (the message it was recorded against)
    alongside the answer, so _dialog_answer can tell a dialog that actually matches it apart
    from an unrelated one firing later - see the JOB-3DA007 tests above."""
    from app.core.browser import _DIALOG_ANSWERS

    class _Page:
        def on(self, *args, **kwargs):
            pass

    page = _Page()
    try:
        _arm_dialog(page, {"value": "accept",
                           "description": "Credit Limit for NOKIA... has exhausted"})
        cell = _DIALOG_ANSWERS[page]
        assert cell[0] is True
        assert cell[2] == "Credit Limit for NOKIA... has exhausted"

        # Re-arming (a second `dialog` step on the same page) updates all three in place.
        _arm_dialog(page, {"value": "dismiss", "description": "Discard changes?"})
        cell = _DIALOG_ANSWERS[page]
        assert cell[0] is False
        assert cell[2] == "Discard changes?"
    finally:
        del _DIALOG_ANSWERS[page]

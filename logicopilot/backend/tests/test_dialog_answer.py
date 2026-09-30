"""_dialog_answer decides how to answer a native alert/confirm/prompt that fires during an ERP
replay. Most are armed in advance by a recorded `dialog` step; this covers what happens when
nothing was armed - the case that used to blindly accept everything, including a destructive
"close the form?" prompt that turned out to be the ERP's own rejection of an invalid Save (see
browser.py's _DESTRUCTIVE_DIALOG_WORDS for the live incident)."""
from app.core.browser import _dialog_answer


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
    an UN-ARMED dialog, same as the existing destructive-word default it's replacing."""
    import weakref

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

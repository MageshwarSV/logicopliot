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

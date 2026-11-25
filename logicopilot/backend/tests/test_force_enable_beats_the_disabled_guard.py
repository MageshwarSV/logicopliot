"""A step marked force_enable must not be killed by the "field disabled" guard.

`force_enable` ("Unblock" in the ERP Script recorder) exists for a control the ERP leaves
DISABLED until something the script never does enables it. `_perform_step` honours it - it
strips the attribute before acting:

    el.disabled = false; el.removeAttribute('disabled')

But two guards ran BEFORE that, saw a disabled element, and ended the run:

    if action in ("fill", "autocomplete", "select", "manual_entry") and _element_disabled(...):
        -> "field disabled - blocked" / "the record already exists"

So a padlocked step could never do the one thing the padlock is for.

Found live on production 2026-11-25, JOB-31CBFD. SOFTLINK's Address dropdown is locked by the
ERP after its "Update the Newly selected Importer & its branch into Customer Entity ?" confirm
is declined. force_enable was set on that select at 12:33; the retry at 12:38 still died with
"step 57 select: field disabled", because it never reached _perform_step.

These are source-level checks on purpose. Both guards need a live page, a real <select> and a
running ERP to exercise, none of which exist in this suite - and the failure they protect
against is precisely a guard being reintroduced without the exemption, which reads perfectly
well in isolation. A cheap test that catches that beats an expensive one nobody can run.
"""
import ast
import inspect
import re

from app.core import browser

SOURCE = inspect.getsource(browser)
ACTIONS = '("fill", "autocomplete", "select", "manual_entry")'


def _guard_blocks() -> list[str]:
    """Every place that ends a step because the element is disabled, as source text."""
    out = []
    for m in re.finditer(re.escape(ACTIONS), SOURCE):
        start = SOURCE.rfind("\n", 0, m.start())
        end = SOURCE.find(":\n", m.end())
        block = SOURCE[start:end if end != -1 else m.end()]
        if "_element_disabled" in block:
            out.append(block)
    return out


def test_both_disabled_guards_exist_and_are_found():
    """If this drops to 0 the test below is checking nothing - fail loudly instead."""
    assert len(_guard_blocks()) == 2, (
        f"expected 2 disabled-guards, found {len(_guard_blocks())} - "
        "the guard moved or was reworded; re-point this test rather than deleting it"
    )


def test_every_disabled_guard_lets_a_force_enable_step_through():
    for block in _guard_blocks():
        assert "force_enable" in block, (
            "a 'field disabled' guard runs without checking force_enable:\n"
            f"{block.strip()}\n"
            "A step the Super Admin padlocked must reach _perform_step, which is what "
            "actually strips the disabled attribute."
        )


def test_perform_step_still_strips_the_attribute():
    """The other half: the guard exemption is pointless if nothing unlocks the element."""
    src = inspect.getsource(browser._perform_step)
    assert 'step.get("force_enable")' in src
    assert "removeAttribute('disabled')" in src


def test_browser_module_still_parses():
    """Cheap insurance - these guards are edited by hand and sit deep in an 8k-line file."""
    ast.parse(SOURCE)

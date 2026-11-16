"""Choosing "Chennai" in a filtering dropdown must choose Chennai - not the option under it.

Found on the ERP's branch box at login (a react-select): the list offered "Chennai" and
"Chennai FTWZ", the job said Chennai, and the run went in as Chennai FTWZ. The dropdown already
highlights its first option when the list opens, and the runner pressed ArrowDown once more "to
reach the first item" before Enter - which lands on the second.

Driven through a real browser against small stand-ins for the two families of widget:
  - highlights the first option as soon as the list opens (react-select, select2);
  - highlights nothing until an arrow key is pressed (jQuery UI autocomplete).
"""
import pytest

pw = pytest.importorskip("playwright.sync_api")

from app.core.browser import _perform_step  # noqa: E402

PAGE = """
<html><body>
<div id="ddlLocation"><div class="react-select__control">
  <div class="react-select__single-value" id="shown"></div>
  <input id="search" autocomplete="off">
</div><div id="menu"></div></div>
<script>
const OPTIONS = %(options)s, PREFOCUS = %(prefocus)s, PREFIX = %(prefix)s;
const input = document.getElementById('search'), menu = document.getElementById('menu');
let shown = [], at = -1;
function draw() {
  menu.innerHTML = '';
  shown.forEach((o, i) => {
    const d = document.createElement('div');
    d.className = PREFIX + '__option' + (i === at ? ' ' + PREFIX + '__option--is-focused' : '');
    d.textContent = o;
    d.addEventListener('mousedown', () => pick(i));
    menu.appendChild(d);
  });
}
function pick(i) {
  if (i < 0 || i >= shown.length) return;
  document.getElementById('shown').textContent = shown[i];
  input.value = ''; shown = []; at = -1; draw();
}
input.addEventListener('input', () => {
  const q = input.value.toLowerCase();
  shown = q ? OPTIONS.filter(o => o.toLowerCase().includes(q)) : [];
  at = (PREFOCUS && shown.length) ? 0 : -1;
  draw();
});
input.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { at = Math.min(at + 1, shown.length - 1); draw(); e.preventDefault(); }
  if (e.key === 'ArrowUp') { at = Math.max(at - 1, 0); draw(); e.preventDefault(); }
  if (e.key === 'Enter') { pick(at); e.preventDefault(); }
});
document.querySelector('.react-select__control').addEventListener('click', () => input.focus());
</script></body></html>
"""

BRANCHES = ["Bangalore", "Chennai", "Chennai FTWZ", "Chennai New", "Mumbai"]


@pytest.fixture(scope="module")
def browser():
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except Exception as exc:  # noqa: BLE001 - no browser on this machine
        pytest.skip(f"no browser available: {exc}")


def _choose(browser, value, *, prefocus, options=BRANCHES, prefix="react-select"):
    import json

    page = browser.new_page()
    try:
        page.set_content(PAGE % {"options": json.dumps(options), "prefocus": json.dumps(prefocus),
                                 "prefix": json.dumps(prefix)})
        step = {"action": "manual_entry", "selector": "#ddlLocation .react-select__control",
                "field_label": "Destination"}
        _perform_step(page, step, value)
        return page.inner_text("#shown")
    finally:
        page.close()


@pytest.mark.parametrize("prefocus", [True, False], ids=["first-option-highlighted", "nothing-highlighted"])
@pytest.mark.parametrize("value", ["Chennai", "Chennai FTWZ", "Chennai New", "Bangalore", "Mumbai"])
def test_the_option_chosen_is_the_one_asked_for(browser, value, prefocus):
    assert _choose(browser, value, prefocus=prefocus) == value


@pytest.mark.parametrize("prefocus", [True, False], ids=["first-option-highlighted", "nothing-highlighted"])
def test_exact_option_wins_wherever_it_sits_in_the_list(browser, prefocus):
    # the exact option is LAST of the three the typing leaves on screen
    options = ["Chennai FTWZ", "Chennai New", "Chennai"]
    assert _choose(browser, "Chennai", prefocus=prefocus, options=options) == "Chennai"


@pytest.mark.parametrize("prefocus", [True, False], ids=["first-option-highlighted", "nothing-highlighted"])
def test_a_case_difference_still_finds_the_option(browser, prefocus):
    assert _choose(browser, "chennai ftwz", prefocus=prefocus) == "Chennai FTWZ"


def test_a_widget_whose_highlight_cannot_be_seen_is_still_right(browser):
    # A dropdown that pre-highlights its first option but marks it with a class nothing
    # recognises: the keyboard lands one too low, and the check afterwards has to catch it.
    page = browser.new_page()
    try:
        import json

        html = (PAGE % {"options": json.dumps(BRANCHES), "prefocus": "true",
                        "prefix": json.dumps("react-select")}).replace("--is-focused", "--zz")
        page.set_content(html)
        _perform_step(page, {"action": "manual_entry",
                             "selector": "#ddlLocation .react-select__control"}, "Chennai")
        assert page.inner_text("#shown") == "Chennai"
    finally:
        page.close()

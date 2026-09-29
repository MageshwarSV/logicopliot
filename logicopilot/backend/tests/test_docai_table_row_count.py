"""_table_row_count (app/core/docai.py) - the "layout analyser": a deterministic count of a
Document AI table's real DATA rows, excluding a trailing TOTAL/SUBTOTAL/GRAND TOTAL row - the
one geometric signal run_extraction's text-vs-vision row cross-check (app/api/v1/jobs.py) uses
as a third, independent source of truth alongside its two LLM reads.

No existing test file constructs fake Document AI proto objects, so this sets the convention:
plain SimpleNamespace stand-ins (matching the rest of the suite's hand-written-fixture style),
built through a small local TextBuilder that keeps each cell's text_anchor offsets consistent
with a single shared full_text string - the same shape _layout_text (docai.py) reads from."""
from types import SimpleNamespace

from app.core.docai import _table_row_count


class TextBuilder:
    """Builds a single full_text string and hands back Document AI-shaped cell/row objects
    whose text_anchor offsets point into it - the same contract _layout_text relies on."""

    def __init__(self):
        self.text = ""

    def cell(self, s: str):
        start = len(self.text)
        self.text += s
        seg = SimpleNamespace(start_index=start, end_index=len(self.text))
        anchor = SimpleNamespace(text_segments=[seg])
        return SimpleNamespace(layout=SimpleNamespace(text_anchor=anchor))

    def row(self, *cell_texts: str):
        return SimpleNamespace(cells=[self.cell(t) for t in cell_texts])


def test_counts_a_clean_table_with_no_summary_row():
    b = TextBuilder()
    rows = [
        b.row("1", "Widget A", "10", "5.00"),
        b.row("2", "Widget B", "20", "3.00"),
        b.row("3", "Widget C", "30", "1.00"),
    ]
    table = SimpleNamespace(body_rows=rows)
    assert _table_row_count(table, b.text) == 3


def test_excludes_a_trailing_total_row_named_by_keyword():
    b = TextBuilder()
    rows = [
        b.row("1", "Widget A", "10", "5.00"),
        b.row("2", "Widget B", "20", "3.00"),
        b.row("TOTAL", "", "30", "50.00"),
    ]
    table = SimpleNamespace(body_rows=rows)
    assert _table_row_count(table, b.text) == 2


def test_excludes_a_trailing_grand_total_row_case_insensitively():
    b = TextBuilder()
    rows = [b.row("1", "Widget A", "10", "5.00"), b.row("Grand Total:", "", "", "50.00")]
    table = SimpleNamespace(body_rows=rows)
    assert _table_row_count(table, b.text) == 1


def test_excludes_a_trailing_row_thats_blank_except_a_number_in_the_last_cell():
    b = TextBuilder()
    rows = [b.row("1", "Widget A", "10", "5.00"), b.row("", "", "", "50.00")]
    table = SimpleNamespace(body_rows=rows)
    assert _table_row_count(table, b.text) == 1


def test_a_summary_looking_word_outside_the_last_row_is_never_excluded():
    """Only the LAST row is ever checked - a genuine total line is always the table's final
    row, never one earlier - so a row whose first cell happens to read "Total" (a real
    customer named "Total Logistics", say) is never mistaken for the table's own summary row
    just because it matches the keyword, as long as it isn't the final row."""
    b = TextBuilder()
    rows = [
        b.row("Total Logistics Inc", "Widget A", "10", "5.00"),
        b.row("2", "Widget B", "20", "3.00"),
    ]
    table = SimpleNamespace(body_rows=rows)
    assert _table_row_count(table, b.text) == 2


def test_empty_table_counts_zero():
    table = SimpleNamespace(body_rows=[])
    assert _table_row_count(table, "") == 0

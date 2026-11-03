"""A per-row field's answer must land on the RIGHT product line, or not at all.

Found on JOB-FC08FF: an invoice with an HTS CODE column on every page produced an empty
Document CTH on all 144 product lines. Three separate things had to go wrong together, and
every one of them failed SILENTLY - the screen simply said the document carried no code.

  - the call had a 45s timeout, which a 144-line invoice overran
  - the reply was one flat 144-element array, which sent the model into a repetition loop
    that ran past any token cap
  - either failure throws, and the except returns a blank for every line in the batch

What makes this worth testing rather than just fixing is the shape of the answer. A flat
list has to be the right length in the right order or every value after a mistake lands on
the wrong product - and a tariff code on the wrong product is a wrong customs declaration
for both of them. Keyed by row number, that cannot happen.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.llm import PER_ROW_BATCH, compute_custom_field_per_row


def _rows(n, start=1):
    return [{"row": i, "part_code": f"P{i}", "description": f"d{i}", "quantity": "1"}
            for i in range(start, start + n)]


def _reply(payload):
    """One OpenAI response carrying `payload` as its JSON content."""
    resp = MagicMock()
    resp.choices[0].message.content = json.dumps(payload)
    resp.choices[0].finish_reason = "stop"
    return resp


def _run(rows, replies):
    """Drive compute_custom_field_per_row with a canned reply per call."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        r = replies[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    with patch("app.core.llm.get_settings") as settings, \
            patch("app.core.llm.create_chat_completion_with_retry", side_effect=_fake):
        settings.return_value.openai_api_key = "sk-test"
        settings.return_value.openai_model = "gpt-4o-mini"
        out = compute_custom_field_per_row("find the code", "doc text", rows)
    return out, calls


def test_a_keyed_answer_lands_on_its_own_row():
    rows = _rows(3)
    out, _ = _run(rows, [_reply({"answers": {"1": "85423300", "2": "", "3": "85411000"}})])
    assert out == ["85423300", "", "85411000"]


def test_the_keys_decide_the_order_not_the_sequence_they_arrive_in():
    # The whole point of keying. Out-of-order keys used to be impossible to detect in a flat
    # list - they just silently shifted every later product's code along by one.
    rows = _rows(3)
    out, _ = _run(rows, [_reply({"answers": {"3": "C", "1": "A", "2": "B"}})])
    assert out == ["A", "B", "C"]


def test_an_answer_for_a_row_we_never_asked_about_is_ignored():
    # The model can see the WHOLE document while being asked about a slice of it, so it does
    # sometimes answer for rows further down the page. Those must not be absorbed.
    rows = _rows(2)
    out, _ = _run(rows, [_reply({"answers": {"1": "A", "2": "B", "7": "STRAY", "8": "STRAY"}})])
    assert out == ["A", "B"]


def test_a_missing_key_blanks_only_that_row():
    rows = _rows(4)
    out, _ = _run(rows, [_reply({"answers": {"1": "A", "4": "D"}})])
    assert out == ["A", "", "", "D"]


def test_every_line_gets_an_answer_slot_even_when_the_call_fails():
    rows = _rows(3)
    out, _ = _run(rows, [TimeoutError("request timed out")])
    assert out == ["", "", ""]
    assert len(out) == len(rows)


def test_a_long_invoice_is_asked_for_in_batches():
    rows = _rows(PER_ROW_BATCH * 2 + 5)
    replies = [
        _reply({"answers": {str(r["row"]): f"v{r['row']}" for r in rows[i: i + PER_ROW_BATCH]}})
        for i in range(0, len(rows), PER_ROW_BATCH)
    ]
    out, calls = _run(rows, replies)
    assert len(calls) == 3
    assert out == [f"v{r['row']}" for r in rows]


def test_one_failed_batch_costs_only_its_own_lines():
    # Before batching, a single timeout blanked the entire invoice. It should now cost the
    # lines in that batch and nothing else.
    rows = _rows(PER_ROW_BATCH * 2)
    good = _reply({"answers": {str(r["row"]): "X" for r in rows[PER_ROW_BATCH:]}})
    out, _ = _run(rows, [TimeoutError("boom"), good])
    assert out[:PER_ROW_BATCH] == [""] * PER_ROW_BATCH
    assert out[PER_ROW_BATCH:] == ["X"] * PER_ROW_BATCH


def test_the_batch_is_small_enough_that_a_reply_comfortably_fits():
    # A batch governs both how long one call runs and how long its reply is, and overrunning
    # either one returns blanks rather than an error. Forty-odd 8-digit codes overran; twenty
    # did not.
    assert PER_ROW_BATCH <= 25


def test_a_model_that_answers_in_the_old_flat_shape_still_works():
    rows = _rows(3)
    out, _ = _run(rows, [_reply({"values": ["A", "B", "C"]})])
    assert out == ["A", "B", "C"]


@pytest.mark.parametrize(
    "values,expected",
    [
        # Short replies pad at the END, where an overlooked last line would fall - never at
        # the front, which would shift every correct answer onto the wrong product.
        (["A"], ["A", "", ""]),
        (["A", "B", "C", "D"], ["A", "B", "C"]),
    ],
)
def test_the_old_flat_shape_never_shifts_an_answer_onto_the_wrong_line(values, expected):
    out, _ = _run(_rows(3), [_reply({"values": values})])
    assert out == expected


def test_no_rows_means_no_call_at_all():
    out, calls = _run([], [])
    assert out == []
    assert calls == []

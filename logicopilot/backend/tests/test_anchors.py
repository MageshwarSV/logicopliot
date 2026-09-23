"""Geometric anchor detection: reject a nearby candidate that looks like data (a number, a
date, another field's value) and keep searching, but never regress a field that already had
a real caption right next to it."""

from app.core.anchors import _looks_like_data, detect_value_and_anchor


def _token(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_looks_like_data_rejects_pure_numbers():
    assert _looks_like_data("283485") is True
    assert _looks_like_data("121013657 Line # 1") is True


def test_looks_like_data_accepts_a_colon_terminated_label_even_if_short():
    assert _looks_like_data("Total:") is False
    assert _looks_like_data("Gross Weight:") is False


def test_looks_like_data_rejects_empty():
    assert _looks_like_data("") is True
    assert _looks_like_data("   ") is True


def test_detect_anchor_skips_a_number_directly_above_and_finds_the_real_caption():
    # Column header "Package Count" sits a couple of rows further above; a stray row total
    # "283485" sits immediately above the value box - exactly the bug seen in production.
    box = {"x0": 0.40, "y0": 0.50, "x1": 0.55, "y1": 0.53}
    tokens = [
        _token("Package", 0.40, 0.40, 0.47, 0.43),
        _token("Count", 0.48, 0.40, 0.55, 0.43),
        _token("283485", 0.40, 0.46, 0.55, 0.49),  # a data row total, nearest to the box
        _token("42", 0.40, 0.505, 0.44, 0.525),  # the value itself, inside the box
    ]
    value, anchor = detect_value_and_anchor({"tokens": tokens}, box)
    assert value == "42"
    assert anchor == "Package Count"


def test_detect_anchor_still_prefers_the_immediate_left_caption_when_it_is_a_real_label():
    # No regression: a same-row "Invoice No:" caption right next to the box must still win
    # immediately, exactly as before this fix.
    box = {"x0": 0.30, "y0": 0.10, "x1": 0.50, "y1": 0.13}
    tokens = [
        _token("Invoice", 0.05, 0.10, 0.15, 0.13),
        _token("No:", 0.16, 0.10, 0.22, 0.13),
        _token("INV-9821", 0.30, 0.10, 0.48, 0.13),
    ]
    value, anchor = detect_value_and_anchor({"tokens": tokens}, box)
    assert value == "INV-9821"
    assert anchor == "Invoice No"


def test_detect_anchor_falls_back_to_nearest_when_nothing_label_like_exists_anywhere():
    # Nothing but numbers in range, in any direction - must still return SOMETHING rather
    # than silently giving up (old behaviour preserved as the last resort).
    box = {"x0": 0.40, "y0": 0.50, "x1": 0.55, "y1": 0.53}
    tokens = [
        _token("99999", 0.40, 0.46, 0.55, 0.49),
        _token("42", 0.40, 0.505, 0.44, 0.525),
    ]
    value, anchor = detect_value_and_anchor({"tokens": tokens}, box)
    assert value == "42"
    assert anchor == "99999"


def test_detect_anchor_with_no_tokens_returns_none_none():
    assert detect_value_and_anchor({"tokens": []}, {"x0": 0, "y0": 0, "x1": 1, "y1": 1}) == (None, None)

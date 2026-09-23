"""locate_value_bbox — finds a value's own text within a REAL document's OCR word boxes,
instead of trusting FieldMark's static template-drawn position (which is only right when a
real document happens to share the template's original sample layout)."""
from app.core.docai import locate_value_bbox


def _tok(text, x0, y0, x1, y1):
    return {"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_finds_a_single_token_value():
    page_tokens = [(1, [_tok("Invoice", 0.1, 0.1, 0.2, 0.12), _tok("EVERETT", 0.3, 0.2, 0.4, 0.22)])]
    result = locate_value_bbox(page_tokens, "EVERETT")
    assert result == (1, 0.3, 0.2, 0.4 - 0.3, 0.22 - 0.2)


def test_finds_a_multi_token_value_and_unions_the_boxes():
    page_tokens = [(1, [
        _tok("Port", 0.1, 0.5, 0.15, 0.52),
        _tok("of", 0.16, 0.5, 0.19, 0.52),
        _tok("delivery:", 0.2, 0.5, 0.3, 0.52),
        _tok("Chicago,", 0.31, 0.5, 0.4, 0.52),
        _tok("USA", 0.41, 0.5, 0.46, 0.52),
    ])]
    result = locate_value_bbox(page_tokens, "Chicago, USA")
    assert result is not None
    page, x, y, w, h = result
    assert page == 1
    assert round(x, 5) == 0.31
    assert round(x + w, 5) == 0.46
    assert round(y, 5) == 0.5
    assert round(y + h, 5) == 0.52


def test_ignores_punctuation_and_case_differences():
    page_tokens = [(1, [_tok("chicago", 0.1, 0.1, 0.2, 0.12), _tok("usa.", 0.21, 0.1, 0.3, 0.12)])]
    result = locate_value_bbox(page_tokens, "CHICAGO, USA")
    assert result is not None


def test_finds_value_on_the_second_page_not_the_first():
    page_tokens = [
        (1, [_tok("Nothing", 0.1, 0.1, 0.2, 0.12), _tok("relevant", 0.21, 0.1, 0.3, 0.12)]),
        (2, [_tok("EVERETT", 0.4, 0.4, 0.5, 0.42)]),
    ]
    result = locate_value_bbox(page_tokens, "EVERETT")
    assert result[0] == 2


def test_no_confident_match_returns_none():
    page_tokens = [(1, [_tok("Something", 0.1, 0.1, 0.2, 0.12), _tok("Else", 0.21, 0.1, 0.3, 0.12)])]
    assert locate_value_bbox(page_tokens, "USD FIVE THOUSAND THREE") is None


def test_empty_value_returns_none():
    page_tokens = [(1, [_tok("Anything", 0.1, 0.1, 0.2, 0.12)])]
    assert locate_value_bbox(page_tokens, None) is None
    assert locate_value_bbox(page_tokens, "") is None


def test_empty_page_tokens_returns_none():
    assert locate_value_bbox([], "EVERETT") is None

"""Geometric anchor detection over cached Document AI tokens.

Given a page's OCR tokens (normalized 0-1 coords) and a crop box, find:
- value_text:  the OCR text inside the box (the example value the admin marked)
- anchor_term: the nearest label-like text just LEFT of or ABOVE the box
Pure geometry — no API calls.
"""

LEFT_SEARCH_WIDTH = 0.30
LEFT_SEARCH_WIDTH_WIDE = 0.45
ABOVE_SEARCH_FACTOR = 3.0
ABOVE_SEARCH_MIN = 0.06
ABOVE_SEARCH_FACTOR_WIDE = 8.0
LINE_GROUP_TOLERANCE = 0.012


def _center(t: dict) -> tuple[float, float]:
    return ((t["x0"] + t["x1"]) / 2, (t["y0"] + t["y1"]) / 2)


def _tokens_inside(tokens: list[dict], box: dict) -> list[dict]:
    out = []
    for t in tokens:
        cx, cy = _center(t)
        if box["x0"] <= cx <= box["x1"] and box["y0"] <= cy <= box["y1"]:
            out.append(t)
    return out


def _join_line(tokens: list[dict]) -> str:
    return " ".join(t["text"] for t in sorted(tokens, key=lambda t: t["x0"]))


def _group_into_lines(tokens: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for token in sorted(tokens, key=lambda t: _center(t)[1]):
        cy = _center(token)[1]
        placed = False
        for line in lines:
            line_cy = sum(_center(t)[1] for t in line) / len(line)
            if abs(cy - line_cy) <= LINE_GROUP_TOLERANCE:
                line.append(token)
                placed = True
                break
        if not placed:
            lines.append([token])
    return lines


def _looks_like_data(text: str) -> bool:
    """A candidate anchor line that is mostly digits (an amount, a date, a code, another
    field's value sitting one row up) is almost certainly NOT this field's caption. A real
    label either ends with ':' or is mostly letters - reject anything digit-heavy so the
    search keeps looking rather than reporting a number as the field's name."""
    t = text.strip()
    if not t:
        return True
    if t.endswith(":"):
        return False
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return digits > letters


def detect_value_and_anchor(ocr: dict, box: dict) -> tuple[str | None, str | None]:
    """Returns (value_text, anchor_term) for a crop box against a page's OCR tokens."""
    tokens = ocr.get("tokens", [])
    if not tokens:
        return None, None

    inside = _tokens_inside(tokens, box)
    value_text = _join_line(inside) if inside else None
    inside_ids = {id(t) for t in inside}
    others = [t for t in tokens if id(t) not in inside_ids]

    box_height = box["y1"] - box["y0"]

    def left_lines(max_width: float) -> list[list[dict]]:
        cands = [
            t for t in others
            if t["x1"] <= box["x0"] + 0.005
            and box["x0"] - t["x1"] <= max_width
            and box["y0"] - 0.01 <= _center(t)[1] <= box["y1"] + 0.01
        ]
        return sorted(_group_into_lines(cands), key=lambda line: box["x0"] - max(t["x1"] for t in line))

    def above_lines(max_range: float) -> list[list[dict]]:
        cands = [
            t for t in others
            if t["y1"] <= box["y0"] + 0.005
            and box["y0"] - t["y1"] <= max_range
            and box["x0"] - 0.10 <= _center(t)[0] <= box["x1"] + 0.10
        ]
        return sorted(_group_into_lines(cands), key=lambda line: box["y0"] - max(t["y1"] for t in line))

    def pick(lines: list[list[dict]], require_label: bool) -> str | None:
        for line in lines:
            text = _join_line(line)
            if not require_label or not _looks_like_data(text):
                return text.strip().rstrip(":").strip() or None
        return None

    above_range = max(ABOVE_SEARCH_FACTOR * box_height, ABOVE_SEARCH_MIN)
    above_range_wide = max(ABOVE_SEARCH_FACTOR_WIDE * box_height, ABOVE_SEARCH_MIN)

    # Same-row caption first, then a column header above - both restricted to label-looking
    # text. Only if nothing label-like turns up anywhere (even after widening the search) do
    # we fall back to the plain nearest-token behaviour, so a field with genuinely no caption
    # nearby still gets something rather than nothing.
    anchor_term = (
        pick(left_lines(LEFT_SEARCH_WIDTH), True)
        or pick(above_lines(above_range), True)
        or pick(left_lines(LEFT_SEARCH_WIDTH_WIDE), True)
        or pick(above_lines(above_range_wide), True)
        or pick(left_lines(LEFT_SEARCH_WIDTH), False)
        or pick(above_lines(above_range), False)
    )

    return value_text, anchor_term

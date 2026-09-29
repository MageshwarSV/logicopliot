"""Google Cloud Document AI OCR service."""

import json
import logging
import os
import re
from pathlib import Path

from app.core.config import get_settings
from app.core.structure_engine import build_structured_text

logger = logging.getLogger(__name__)

_client = None


def _resolve_credentials() -> None:
    settings = get_settings()
    creds = settings.google_application_credentials
    if not creds:
        return
    path = Path(creds)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent.parent / path
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)


def _get_client():
    global _client
    if _client is None:
        _resolve_credentials()
        from google.api_core.client_options import ClientOptions
        from google.cloud import documentai

        settings = get_settings()
        opts = ClientOptions(api_endpoint=f"{settings.docai_location}-documentai.googleapis.com")
        _client = documentai.DocumentProcessorServiceClient(client_options=opts)
    return _client


def _layout_text(layout, full_text: str) -> str:
    parts = []
    for seg in layout.text_anchor.text_segments:
        parts.append(full_text[int(seg.start_index): int(seg.end_index)])
    return "".join(parts)


def _segments(layout) -> list[tuple[int, int]]:
    return [(int(s.start_index), int(s.end_index)) for s in layout.text_anchor.text_segments]


def _top_y(layout) -> float:
    verts = layout.bounding_poly.normalized_vertices
    return min((v.y for v in verts), default=0.0)


def _bbox(layout) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1), normalized 0..1 - the full box, not just its top edge (_top_y
    above only ever needed that one value; structure_engine.py needs all four)."""
    verts = layout.bounding_poly.normalized_vertices
    xs = [v.x for v in verts]
    ys = [v.y for v in verts]
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


_SUMMARY_ROW_WORDS = re.compile(
    r"^(total|sub.?total|grand.?total|net amount|amount in words)\b", re.IGNORECASE
)


def _looks_numeric(text: str) -> bool:
    return bool(re.search(r"\d", text)) and not re.search(r"[a-zA-Z]{3,}", text)


def _is_summary_row(cells: list[str]) -> bool:
    """A table's own TOTAL/SUBTOTAL row, printed as one more ruled row of the same table
    Document AI detected - structurally indistinguishable from a product row at the layout
    level, so this is the one place actually deciding "is this a row of DATA or a row that
    SUMS the data above it", from the row's own text alone (no LLM call). Two shapes: the
    row's first non-empty cell literally says Total/Subtotal/Grand Total/Net Amount, or every
    cell but the last is blank while the last cell holds a number (a total printed in the
    table's rightmost/amount column with nothing else on that line)."""
    non_empty = [c for c in cells if c.strip()]
    if not non_empty:
        return False
    if _SUMMARY_ROW_WORDS.match(non_empty[0].strip()):
        return True
    if len(non_empty) == 1 and cells and cells[-1].strip() and _looks_numeric(cells[-1]):
        return True
    return False


def _table_row_count(table, full_text: str) -> int:
    """How many real DATA rows this Document AI table actually has - the layout analyser: a
    cheap, deterministic geometry check (not another LLM call) that gives run_extraction's
    text-vs-vision row cross-check (app/api/v1/jobs.py) a third, independent source of truth
    for "how many rows are there really", instead of two LLM reads only ever arguing with each
    other. Excludes a trailing summary row Document AI's own table detection has no concept of
    (see _is_summary_row) - it only ever looks at the LAST row, since a genuine total line is
    always the table's final row, never a row in the middle."""
    body = table.body_rows
    if not body:
        return 0
    last_cells = [_layout_text(c.layout, full_text).strip() for c in body[-1].cells]
    return len(body) - 1 if _is_summary_row(last_cells) else len(body)


def _table_markdown(table, full_text: str) -> str:
    """One Document AI table, as a Markdown table. Cells are read by their own text_anchor,
    not by position in the page's word stream — that is what keeps a column a column instead
    of interleaving with whatever sits beside it on the page."""
    def row_cells(row) -> list[str]:
        return [_layout_text(c.layout, full_text).strip().replace("\n", " ") for c in row.cells]

    header = [row_cells(r) for r in table.header_rows]
    body = [row_cells(r) for r in table.body_rows]
    if not header and body:
        # No declared header row — use the first body row as one so this is still valid
        # Markdown; a table rendered with no header line is a wall of unaligned pipes.
        header, body = [body[0]], body[1:]
    if not header:
        return ""
    width = len(header[0])
    lines = ["| " + " | ".join(header[0]) + " |", "|" + "|".join(["---"] * width) + "|"]
    for row in body:
        cells = (row + [""] * width)[:width]  # a ragged row must not shift the columns after it
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _page_layout_text(page, full_text: str) -> str:
    """Document AI already groups this page into paragraphs and tables and keeps each one's
    own text together — this reassembles the page from THOSE, top-to-bottom, instead of the
    flat word-by-word stream `document.text` concatenates. That flat stream is what
    interleaves a table's columns or a multi-column page's two halves into one scrambled
    line; grouped by paragraph/cell first, a value stays attached to the field beside it.

    Returns "" (never raises) when the page has no paragraphs or tables to work from — some
    processor types don't populate them — so the caller can fall back to the flat text.
    """
    try:
        items: list[tuple[float, str]] = []
        table_spans: list[tuple[int, int]] = []
        for table in page.tables:
            md = _table_markdown(table, full_text)
            if not md:
                continue
            items.append((_top_y(table.layout), md))
            table_spans.extend(_segments(table.layout))

        def _inside_a_table(paragraph) -> bool:
            # A paragraph the page ALSO lists separately for text that a table already
            # covers would otherwise print every cell's words a second time, right next to
            # the table itself.
            for start, end in _segments(paragraph.layout):
                mid = (start + end) // 2
                if any(t_start <= mid < t_end for t_start, t_end in table_spans):
                    return True
            return False

        for para in page.paragraphs:
            if _inside_a_table(para):
                continue
            text = _layout_text(para.layout, full_text).strip()
            if text:
                items.append((_top_y(para.layout), text))

        if not items:
            return ""
        items.sort(key=lambda x: x[0])
        return "\n\n".join(text for _, text in items)
    except Exception:  # noqa: BLE001 — layout formatting is a bonus, never a hard dependency
        logger.exception("layout-aware formatting failed; falling back to flat OCR text")
        return ""


def _page_blocks(page, full_text: str) -> list[dict]:
    """The same paragraphs/tables _page_layout_text reads, as plain dicts with their own
    bounding box - structure_engine.py's input. Kept separate from _page_layout_text itself
    (which stays exactly as it was) rather than folding the two together, so the existing,
    already-relied-upon function is never at risk from this addition."""
    blocks: list[dict] = []
    table_spans: list[tuple[int, int]] = []
    for table in page.tables:
        md = _table_markdown(table, full_text)
        if not md:
            continue
        x0, y0, x1, y1 = _bbox(table.layout)
        blocks.append({"kind": "table", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": md})
        table_spans.extend(_segments(table.layout))

    def _inside_a_table(paragraph) -> bool:
        for start, end in _segments(paragraph.layout):
            mid = (start + end) // 2
            if any(t_start <= mid < t_end for t_start, t_end in table_spans):
                return True
        return False

    for para in page.paragraphs:
        if _inside_a_table(para):
            continue
        text = _layout_text(para.layout, full_text).strip()
        if not text:
            continue
        x0, y0, x1, y1 = _bbox(para.layout)
        blocks.append({"kind": "paragraph", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text})
    return blocks


def _table_row_counts_for_page(page, full_text: str) -> list[int]:
    """One entry per Document AI table detected on this page - see _table_row_count. Empty
    list when the page has no tables (a borderless/whitespace-aligned layout Document AI's
    table detector never turns into a `table` object at all) or on any failure - this is a
    bonus signal, same fail-safe contract as _page_layout_text, never a hard dependency."""
    try:
        return [_table_row_count(t, full_text) for t in page.tables]
    except Exception:  # noqa: BLE001
        logger.exception("table row count failed; falling back to no detected-row signal")
        return []


_OCR_MAX_SIDE = 2500
_OCR_REENCODE_BYTES = 8 * 1024 * 1024


def _docai_safe_bytes(image_path: Path) -> tuple[bytes, str]:
    raw = image_path.read_bytes()
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(image_path))
        page = doc[0]
        longest = max(page.rect.width, page.rect.height) or 1
        scale = min(1.0, _OCR_MAX_SIDE / longest)
        if scale < 1.0 or len(raw) > _OCR_REENCODE_BYTES:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            content = pix.tobytes("jpeg", jpg_quality=85)
            doc.close()
            return content, "image/jpeg"
        doc.close()
    except Exception:  # noqa: BLE001
        logger.exception("Could not downscale %s for Document AI; sending as-is", image_path)
    return raw, "image/png"


def ocr_page_image(image_path: Path) -> dict:
    from google.cloud import documentai

    settings = get_settings()
    client = _get_client()
    name = client.processor_path(
        settings.docai_project_id, settings.docai_location, settings.docai_processor_id
    )
    content, mime = _docai_safe_bytes(image_path)
    raw = documentai.RawDocument(content=content, mime_type=mime)
    result = client.process_document(
        request=documentai.ProcessRequest(name=name, raw_document=raw)
    )
    doc = result.document

    tokens = []
    layout_text = ""
    table_row_counts: list[int] = []
    structured_text: str | None = None
    debug_blocks: list[dict] = []
    for page in doc.pages:
        for token in page.tokens:
            text = _layout_text(token.layout, doc.text).strip()
            if not text:
                continue
            verts = token.layout.bounding_poly.normalized_vertices
            xs = [v.x for v in verts]
            ys = [v.y for v in verts]
            if not xs or not ys:
                continue
            tokens.append(
                {"text": text, "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}
            )
        # One page is rendered per call here, so at most one page's worth of layout text -
        # still written inside the loop rather than assuming pages[0], in case a future
        # caller ever passes a multi-page image through.
        layout_text += _page_layout_text(page, doc.text)
        table_row_counts.extend(_table_row_counts_for_page(page, doc.text))
        # Computed from the SAME paragraph/table objects layout_text already reads - see
        # structure_engine.py. None whenever it found nothing worth reordering (the common
        # case); a caller reading this must fall back to layout_text in that case, exactly as
        # documented there.
        page_blocks = _page_blocks(page, doc.text)
        debug_blocks.extend(page_blocks)
        page_structured = build_structured_text(page_blocks)
        if page_structured:
            structured_text = (structured_text or "") + page_structured
    return {
        "text": doc.text, "tokens": tokens, "layout_text": layout_text or doc.text,
        # One int per Document AI table detected on this page - the actual row count the
        # LAYOUT itself reports, excluding a trailing TOTAL/summary row (see
        # _table_row_count). Empty when Document AI found no ruled/structured table here
        # (a borderless layout, or a non-table page) - callers treat that as "no signal",
        # never as "zero rows".
        "table_row_counts": table_row_counts,
        # None unless the structure engine actually found and fixed a genuine multi-column
        # region on this page - see structure_engine.py's own docstring for why a page it
        # cannot help returns None rather than a redundant copy of layout_text.
        "structured_text": structured_text,
        # The exact paragraph/table geometry structured_text was computed from - never read
        # by any real caller, only by field_marks.py's own debug=true test-extract path, to
        # inspect real bounding boxes directly instead of guessing at them.
        "debug_blocks": debug_blocks,
    }


def _normalize_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)  # OCR punctuation/spacing rarely matches an AI-cleaned value
    return re.sub(r"\s+", " ", text).strip()


def locate_value_bbox(
    page_tokens: list[tuple[int, list[dict]]], value: str | None,
) -> tuple[int, float, float, float, float] | None:
    """Where a value's own text actually sits on the real page it was read from — a sliding
    window over THIS document's own OCR word boxes (normalized 0-1, the same scale
    FieldMark.x/y/width/height uses), not a template's one-time-drawn position. Every real
    document has its own layout, so a fixed template box is only ever right by coincidence;
    this looks at the actual page.

    Only reports a match it is genuinely confident about (the window's own text equals the
    value, or is within a few characters of it once punctuation/casing/whitespace are
    normalized away). Returns None rather than guess — a caller should show no highlight at
    all rather than one that might be sitting over the wrong thing.
    """
    target = _normalize_for_match(value or "")
    if not target:
        return None
    max_window = 12  # a value is a phrase, not a paragraph — caps the search per start point
    for page_num, tokens in page_tokens:
        texts = [_normalize_for_match(t.get("text", "")) for t in tokens]
        n = len(texts)
        for start in range(n):
            if not texts[start]:
                continue
            joined = ""
            for end in range(start, min(start + max_window, n)):
                joined = f"{joined} {texts[end]}".strip() if joined else texts[end]
                if len(joined) > len(target) + 8:
                    break
                if joined == target or (target in joined and len(joined) - len(target) <= 3):
                    window = tokens[start:end + 1]
                    x0 = min(t["x0"] for t in window)
                    y0 = min(t["y0"] for t in window)
                    x1 = max(t["x1"] for t in window)
                    y1 = max(t["y1"] for t in window)
                    return page_num, x0, y0, x1 - x0, y1 - y0
    return None


def get_page_ocr(document_dir: Path, page_number: int) -> dict:
    cache_file = document_dir / "ocr" / f"page_{page_number}.json"
    if cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if "layout_text" in cached and "table_row_counts" in cached and "structured_text" in cached:
            return cached
        # A cache written before layout-aware formatting (or table_row_counts, or
        # structured_text) existed. Re-read this one page rather than serving every
        # already-processed document its old data forever - the whole point is that jobs
        # already in the system get the improvement too.
        logger.info("%s page %d: OCR cache predates layout formatting, table row counts, "
                    "or the structure engine, re-reading",
                    document_dir.name, page_number)

    image_path = document_dir / "pages" / f"page_{page_number}.png"
    if not image_path.exists():
        raise FileNotFoundError(f"Rendered page image not found for page {page_number}")

    data = ocr_page_image(image_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(cache_file)
    used_layout = data.get("layout_text") and data["layout_text"] != data.get("text")
    logger.info("Document AI OCR cached: %s page %d (%d tokens, layout-formatted=%s)",
               document_dir.name, page_number, len(data["tokens"]), bool(used_layout))
    return data

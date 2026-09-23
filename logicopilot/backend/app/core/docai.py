"""Google Cloud Document AI OCR service."""

import json
import logging
import os
import re
from pathlib import Path

from app.core.config import get_settings

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
    return {"text": doc.text, "tokens": tokens, "layout_text": layout_text or doc.text}


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
        if "layout_text" in cached:
            return cached
        # A cache written before layout-aware formatting existed. Re-read this one page
        # rather than serving every already-processed document its old flat text forever -
        # the whole point is that jobs already in the system get the improvement too.
        logger.info("%s page %d: OCR cache predates layout formatting, re-reading",
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

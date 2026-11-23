"""OCR_ENGINE=pdftext: a digital PDF page's own text + word positions, read locally, serve as its
OCR; a scanned page gives nothing (read from the image); docai/none unchanged."""
from types import SimpleNamespace

import fitz

from app.core import docai


def _set(monkeypatch, engine, tmp_path):
    monkeypatch.setattr(docai, "get_settings",
                        lambda: SimpleNamespace(ocr_engine=engine, uploads_dir=str(tmp_path)))


def _digital_page(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "COMMERCIAL INVOICE No. INV-2026-001")
    page.insert_text((72, 130), "Total Amount USD 4,103.50")
    return doc, page


def test_digital_page_text_and_positions(monkeypatch, tmp_path):
    _set(monkeypatch, "pdftext", tmp_path)
    doc, page = _digital_page(tmp_path)
    png = tmp_path / "pages" / "page_1.png"
    png.parent.mkdir()
    page.get_pixmap(dpi=72).save(png)
    docai.save_text_layer(page, png)
    r = docai.ocr_page_image(png)
    assert "COMMERCIAL INVOICE" in r["text"] and "4,103.50" in r["text"]
    tok = next(t for t in r["tokens"] if t["text"] == "INVOICE")
    assert 0 < tok["x0"] < tok["x1"] < 1 and 0 < tok["y0"] < tok["y1"] < 1
    # the per-document path (review, extraction) reads the same text layer
    assert "INV-2026-001" in docai.get_page_ocr(tmp_path, 1)["text"]
    assert not (tmp_path / "_ocr_by_content").exists()


def test_scanned_page_has_no_text(monkeypatch, tmp_path):
    _set(monkeypatch, "pdftext", tmp_path)
    doc = fitz.open()
    page = doc.new_page()                     # nothing written: like an image-only scan
    png = tmp_path / "page_1.png"
    page.get_pixmap(dpi=72).save(png)
    docai.save_text_layer(page, png)
    assert docai.ocr_page_image(png)["text"] == ""


def test_other_engines_write_nothing(monkeypatch, tmp_path):
    for engine in ("docai", "none"):
        _set(monkeypatch, engine, tmp_path)
        doc, page = _digital_page(tmp_path)
        png = tmp_path / f"{engine}.png"
        page.get_pixmap(dpi=72).save(png)
        docai.save_text_layer(page, png)
        assert not docai.text_layer_path(png).exists()
    _set(monkeypatch, "none", tmp_path)
    assert docai.ocr_page_image(png)["text"] == ""

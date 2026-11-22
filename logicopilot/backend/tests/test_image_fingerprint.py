"""Scanned pages with OCR off: the in-mail duplicate check falls back to a pixel fingerprint."""
from types import SimpleNamespace

import fitz

from app.core import docai, email_puller as ep


def _png(path, text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text)
    page.get_pixmap(dpi=72).save(path)


def test_same_paper_same_fingerprint_different_paper_different(monkeypatch, tmp_path):
    monkeypatch.setattr(docai, "get_settings", lambda: SimpleNamespace(ocr_engine="none"))
    _png(tmp_path / "a1.png", "INVOICE 1001")
    _png(tmp_path / "b1.png", "INVOICE 1001")      # the same paper in another attachment
    _png(tmp_path / "c1.png", "INVOICE 2002")      # a different invoice
    fa = ep._image_fingerprint([(1, tmp_path / "a1.png")], [1])
    fb = ep._image_fingerprint([(1, tmp_path / "b1.png")], [1])
    fc = ep._image_fingerprint([(1, tmp_path / "c1.png")], [1])
    assert fa and fa == fb and fa != fc and fa.startswith("img:")


def test_off_with_document_ai_and_missing_pages(monkeypatch, tmp_path):
    _png(tmp_path / "a1.png", "X")
    monkeypatch.setattr(docai, "get_settings", lambda: SimpleNamespace(ocr_engine="docai"))
    assert ep._image_fingerprint([(1, tmp_path / "a1.png")], [1]) == ""     # production: unchanged
    monkeypatch.setattr(docai, "get_settings", lambda: SimpleNamespace(ocr_engine="pdftext"))
    assert ep._image_fingerprint([(1, tmp_path / "a1.png")], [2]) == ""     # page not there

"""OCR_ENGINE=none: no Document AI call and no OCR cache read/write; "docai" (default) unchanged."""
from types import SimpleNamespace

from app.core import docai


def _settings(monkeypatch, engine, tmp_path):
    monkeypatch.setattr(docai, "get_settings",
                        lambda: SimpleNamespace(ocr_engine=engine, uploads_dir=str(tmp_path)))


def test_none_returns_empty_without_calling_google_or_touching_caches(monkeypatch, tmp_path):
    _settings(monkeypatch, "none", tmp_path)
    monkeypatch.setattr(docai, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("called Google")))
    img = tmp_path / "page_1.png"
    img.write_bytes(b"not-really-a-png")
    (tmp_path / "doc" / "ocr").mkdir(parents=True)
    (tmp_path / "doc" / "ocr" / "page_1.json").write_text('{"text": "CACHED", "layout_text": "x", '
                                                          '"table_row_counts": [], "structured_text": null}')
    r = docai.ocr_page_image(img)
    assert r["text"] == "" and r["tokens"] == [] and r["table_row_counts"] == []
    assert docai.get_page_ocr(tmp_path / "doc", 1)["text"] == ""      # per-doc cache not served
    assert not (tmp_path / "_ocr_by_content").exists()                 # nothing written


def test_default_is_docai(monkeypatch, tmp_path):
    _settings(monkeypatch, "docai", tmp_path)
    assert not docai.ocr_off()
    monkeypatch.setattr(docai, "get_settings", lambda: SimpleNamespace(uploads_dir=str(tmp_path)))
    assert not docai.ocr_off()                                          # unset = docai

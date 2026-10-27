"""POST /classifier-test - a standalone, no-job-required tool for trying the real classifier
against an arbitrary file, gated behind settings.enable_local_classifier_test (off by default
everywhere, including UAT and production - a local-only dev tool, see its own module
docstring)."""
from unittest.mock import patch

import fitz


def _tiny_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def test_404s_when_the_feature_flag_is_off(client):
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings:
        mock_settings.return_value.enable_local_classifier_test = False
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 404


def test_classifies_a_pdf_against_the_default_candidates_when_enabled(client):
    fake_match = [{"key": "Invoice", "confidence": 0.93, "evidence": "Says COMMERCIAL INVOICE at the top"}]
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings, \
         patch("app.api.v1.classifier_test.classify_page_image", return_value=fake_match):
        mock_settings.return_value.enable_local_classifier_test = True
        mock_settings.return_value.uploads_dir = "uploads"
        mock_settings.return_value.vision_engine_model = "gpt-5-mini"
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "gpt-5-mini"
    assert body["candidates"] == ["Bill of lading", "Invoice", "Packing List", "Fright Certificate"]
    assert len(body["results"]) == 1
    page = body["results"][0]["pages"][0]
    assert page["matches"] == [
        {"matched_type": "Invoice", "confidence": 0.93, "evidence": "Says COMMERCIAL INVOICE at the top"}
    ]


def test_a_page_with_no_match_reports_matches_as_null(client):
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings, \
         patch("app.api.v1.classifier_test.classify_page_image", return_value=[]):
        mock_settings.return_value.enable_local_classifier_test = True
        mock_settings.return_value.uploads_dir = "uploads"
        mock_settings.return_value.vision_engine_model = "gpt-5-mini"
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("mystery.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["pages"][0]["matches"] is None


def test_custom_candidates_override_the_default(client):
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings, \
         patch("app.api.v1.classifier_test.classify_page_image", return_value=[]) as mock_classify:
        mock_settings.return_value.enable_local_classifier_test = True
        mock_settings.return_value.uploads_dir = "uploads"
        mock_settings.return_value.vision_engine_model = "gpt-5-mini"
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("doc.pdf", _tiny_pdf_bytes(), "application/pdf")},
            data={"candidates_json": '[{"key": "X", "name": "Custom Type", "doc_type": "Custom"}]'},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == ["Custom Type"]
    called_candidates = mock_classify.call_args[0][3]
    assert called_candidates == [{"key": "X", "name": "Custom Type", "doc_type": "Custom"}]


def test_invalid_candidates_json_is_rejected(client):
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings:
        mock_settings.return_value.enable_local_classifier_test = True
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("doc.pdf", _tiny_pdf_bytes(), "application/pdf")},
            data={"candidates_json": "not json"},
        )
    assert resp.status_code == 422


def test_an_empty_file_is_rejected(client):
    with patch("app.api.v1.classifier_test.get_settings") as mock_settings:
        mock_settings.return_value.enable_local_classifier_test = True
        resp = client.post(
            "/api/v1/classifier-test",
            files={"file": ("doc.pdf", b"", "application/pdf")},
        )
    assert resp.status_code == 422

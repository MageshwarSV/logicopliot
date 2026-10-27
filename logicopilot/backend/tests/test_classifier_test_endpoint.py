"""POST /classifier-test - a standalone, no-job-required tool for trying the real classifier
against one or more arbitrary files. Super-Admin-only, same access control as every other admin
tool in this app - runs on whatever server it's deployed to, using that server's own
already-configured OpenAI key pool (tenant_id=None, the same global-pool fallback every
untenanted call already uses), so it works on UAT/production out of the box with no separate
setup."""
from unittest.mock import patch

import fitz
from tests.conftest import login, make_tenant, make_user


def _tiny_pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    return doc.tobytes()


def test_rejects_an_unauthenticated_request(client):
    resp = client.post(
        "/api/v1/classifier-test",
        files={"files": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 401


def test_rejects_a_non_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-classify@example.com")
    login(client, op.email)
    resp = client.post(
        "/api/v1/classifier-test",
        files={"files": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 403


def test_classifies_a_pdf_against_the_default_candidates(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify@example.com")
    login(client, admin.email)

    fake_match = [{"key": "Invoice", "confidence": 0.93, "evidence": "Says COMMERCIAL INVOICE at the top"}]
    with patch("app.api.v1.classifier_test.classify_page_image", return_value=fake_match):
        resp = client.post(
            "/api/v1/classifier-test",
            files={"files": ("invoice.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidates"] == ["Bill of lading", "Invoice", "Packing List", "Fright Certificate"]
    assert len(body["results"]) == 1
    page = body["results"][0]["pages"][0]
    assert page["matches"] == [
        {"matched_type": "Invoice", "confidence": 0.93, "evidence": "Says COMMERCIAL INVOICE at the top"}
    ]


def test_classifies_several_uploaded_files_independently(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify-multi@example.com")
    login(client, admin.email)

    fake_match = [{"key": "Invoice", "confidence": 0.9, "evidence": "looks like an invoice"}]
    with patch("app.api.v1.classifier_test.classify_page_image", return_value=fake_match):
        resp = client.post(
            "/api/v1/classifier-test",
            files=[
                ("files", ("invoice1.pdf", _tiny_pdf_bytes(), "application/pdf")),
                ("files", ("invoice2.pdf", _tiny_pdf_bytes(), "application/pdf")),
            ],
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["file"] for r in body["results"]] == ["invoice1.pdf", "invoice2.pdf"]


def test_a_page_with_no_match_reports_matches_as_null(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify2@example.com")
    login(client, admin.email)

    with patch("app.api.v1.classifier_test.classify_page_image", return_value=[]):
        resp = client.post(
            "/api/v1/classifier-test",
            files={"files": ("mystery.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["pages"][0]["matches"] is None


def test_custom_candidates_override_the_default(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify3@example.com")
    login(client, admin.email)

    with patch("app.api.v1.classifier_test.classify_page_image", return_value=[]) as mock_classify:
        resp = client.post(
            "/api/v1/classifier-test",
            files={"files": ("doc.pdf", _tiny_pdf_bytes(), "application/pdf")},
            data={"candidates_json": '[{"key": "X", "name": "Custom Type", "doc_type": "Custom"}]'},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates"] == ["Custom Type"]
    called_candidates = mock_classify.call_args[0][3]
    assert called_candidates == [{"key": "X", "name": "Custom Type", "doc_type": "Custom"}]


def test_invalid_candidates_json_is_rejected(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify4@example.com")
    login(client, admin.email)

    resp = client.post(
        "/api/v1/classifier-test",
        files={"files": ("doc.pdf", _tiny_pdf_bytes(), "application/pdf")},
        data={"candidates_json": "not json"},
    )
    assert resp.status_code == 422


def test_an_empty_file_is_reported_as_a_per_file_error_not_a_hard_failure(client, db_session):
    """One bad file in a multi-file upload must not abort the whole batch - see
    test_one_bad_file_does_not_block_the_others_in_the_batch below for the batch case."""
    admin = make_user(db_session, role="super_admin", email="sa-classify5@example.com")
    login(client, admin.email)

    resp = client.post(
        "/api/v1/classifier-test",
        files={"files": ("doc.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == [{"file": "doc.pdf", "error": "Uploaded file is empty."}]


def test_one_bad_file_does_not_block_the_others_in_the_batch(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify6@example.com")
    login(client, admin.email)

    fake_match = [{"key": "Invoice", "confidence": 0.9, "evidence": "looks like an invoice"}]
    with patch("app.api.v1.classifier_test.classify_page_image", return_value=fake_match):
        resp = client.post(
            "/api/v1/classifier-test",
            files=[
                ("files", ("empty.pdf", b"", "application/pdf")),
                ("files", ("good.pdf", _tiny_pdf_bytes(), "application/pdf")),
            ],
        )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results[0] == {"file": "empty.pdf", "error": "Uploaded file is empty."}
    assert results[1]["file"] == "good.pdf"


def test_rejects_an_empty_upload(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-classify7@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/classifier-test", files={})
    assert resp.status_code in (422, 400)

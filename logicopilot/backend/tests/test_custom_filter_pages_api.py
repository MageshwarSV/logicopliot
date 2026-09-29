"""POST/GET/PATCH/DELETE /custom-filter-pages: Super Admin uploads a reference page, OCR'd
once, so it can be matched against future document pages by pure text comparison. Real
multipart upload + real PDF rendering, only OCR (ocr_page_image) is mocked - same pattern
as test_smart_upload.py."""
from pathlib import Path
from unittest.mock import patch

import fitz

from app.api.v1.custom_filter_pages import _sweep_old_jobs_for_reference
from app.core.custom_page_filter import get_active_custom_filter_texts
from app.models.custom_filter_page import CustomFilterPage
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes(n_pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _mock_ocr(text: str = "some boilerplate text"):
    return patch("app.api.v1.custom_filter_pages.ocr_page_image", return_value={"text": text, "tokens": []})


def _login_super_admin(client, db_session, email="sa-cfp@example.com"):
    make_user(db_session, role=SUPER_ADMIN, email=email)
    login(client, email)


def test_non_super_admin_cannot_upload(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-cfp@example.com")
    login(client, "ta-cfp@example.com")

    resp = client.post("/api/v1/custom-filter-pages?name=Cover+Page",
                        files={"file": ("cover.pdf", _pdf_bytes(1), "application/pdf")})
    assert resp.status_code == 403


def test_upload_stores_ocr_text_but_never_returns_it(client, db_session):
    _login_super_admin(client, db_session)

    with _mock_ocr("STANDARD COVER SHEET boilerplate text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Cover+Page",
                            files={"file": ("cover.pdf", _pdf_bytes(1), "application/pdf")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Cover Page"
    assert body["page_count"] == 1
    assert body["is_active"] is True
    assert "reference_text" not in body

    row = db_session.query(CustomFilterPage).filter(CustomFilterPage.id == body["id"]).one()
    assert "STANDARD COVER SHEET" in row.reference_text


def test_upload_multi_page_reference_joins_all_pages_text(client, db_session):
    _login_super_admin(client, db_session)

    with _mock_ocr("page text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Two+Pager",
                            files={"file": ("two.pdf", _pdf_bytes(2), "application/pdf")})
    assert resp.status_code == 201
    assert resp.json()["page_count"] == 2


def test_upload_rejects_disallowed_file_type(client, db_session):
    _login_super_admin(client, db_session)

    resp = client.post("/api/v1/custom-filter-pages?name=Bad",
                        files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 422


def test_upload_rejects_unreadable_file(client, db_session):
    _login_super_admin(client, db_session)

    resp = client.post("/api/v1/custom-filter-pages?name=Broken",
                        files={"file": ("broken.pdf", b"not a real pdf", "application/pdf")})
    assert resp.status_code == 422


def test_upload_rejects_when_ocr_finds_no_text(client, db_session):
    _login_super_admin(client, db_session)

    with _mock_ocr(""):
        resp = client.post("/api/v1/custom-filter-pages?name=Blank",
                            files={"file": ("blank.pdf", _pdf_bytes(1), "application/pdf")})
    assert resp.status_code == 422


def test_list_returns_uploaded_pages(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("text one"):
        client.post("/api/v1/custom-filter-pages?name=Page+One",
                    files={"file": ("one.pdf", _pdf_bytes(1), "application/pdf")})

    resp = client.get("/api/v1/custom-filter-pages")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert "Page One" in names


def test_toggle_active_is_respected_by_get_active_custom_filter_texts(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("toggle-me text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Toggle+Me",
                            files={"file": ("t.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    assert get_active_custom_filter_texts(db_session) == ["toggle-me text"]

    resp = client.patch(f"/api/v1/custom-filter-pages/{page_id}/active", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert get_active_custom_filter_texts(db_session) == []


def test_delete_removes_the_row(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("delete-me text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Delete+Me",
                            files={"file": ("d.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    resp = client.delete(f"/api/v1/custom-filter-pages/{page_id}")
    assert resp.status_code == 204
    assert db_session.get(CustomFilterPage, page_id) is None


def test_delete_of_unknown_id_is_a_no_op(client, db_session):
    _login_super_admin(client, db_session)
    resp = client.delete("/api/v1/custom-filter-pages/does-not-exist")
    assert resp.status_code == 204


def test_preview_returns_the_rendered_page_image(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("preview text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Preview+Me",
                            files={"file": ("p.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    resp = client.get(f"/api/v1/custom-filter-pages/{page_id}/pages/1")
    assert resp.status_code == 200
    # Uploads are compressed to JPEG for storage (see _compress_preview_image) - OCR already
    # ran against the original PNG render before that happens.
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) > 0


def test_preview_rejects_out_of_range_page_number(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("preview text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Preview+Me",
                            files={"file": ("p.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    resp = client.get(f"/api/v1/custom-filter-pages/{page_id}/pages/2")
    assert resp.status_code == 404


def test_preview_of_unknown_page_id_is_404(client, db_session):
    _login_super_admin(client, db_session)
    resp = client.get("/api/v1/custom-filter-pages/does-not-exist/pages/1")
    assert resp.status_code == 404


def test_preview_requires_super_admin(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("preview text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Preview+Me",
                            files={"file": ("p.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-preview@example.com")
    login(client, "ta-preview@example.com")

    resp = client.get(f"/api/v1/custom-filter-pages/{page_id}/pages/1")
    assert resp.status_code == 403


def test_the_size_limit_was_raised_to_50mb():
    from app.api.v1.custom_filter_pages import MAX_FILE_BYTES
    assert MAX_FILE_BYTES == 50 * 1024 * 1024


def test_upload_rejects_a_file_over_the_new_50mb_limit(client, db_session):
    _login_super_admin(client, db_session)
    oversized = b"\x00" * (50 * 1024 * 1024 + 1)
    resp = client.post("/api/v1/custom-filter-pages?name=Too+Big",
                        files={"file": ("huge.pdf", oversized, "application/pdf")})
    assert resp.status_code == 413
    assert "50 MB" in resp.json()["detail"]


def test_uploaded_page_is_stored_as_compressed_jpeg_not_raw_png(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("compress me"):
        resp = client.post("/api/v1/custom-filter-pages?name=Compress+Me",
                            files={"file": ("c.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    from app.core.config import get_settings
    pages_dir = Path(get_settings().uploads_dir) / "custom_filter_pages" / page_id / "pages"
    assert (pages_dir / "page_1.jpg").exists()
    assert not (pages_dir / "page_1.png").exists()


def test_preview_falls_back_to_png_for_a_page_stored_before_compression_existed(client, db_session):
    # Simulates a reference page uploaded before this change - only a .png on disk, no .jpg.
    _login_super_admin(client, db_session)
    with _mock_ocr("legacy text"):
        resp = client.post("/api/v1/custom-filter-pages?name=Legacy+Page",
                            files={"file": ("legacy.pdf", _pdf_bytes(1), "application/pdf")})
    page_id = resp.json()["id"]

    from app.core.config import get_settings
    pages_dir = Path(get_settings().uploads_dir) / "custom_filter_pages" / page_id / "pages"
    (pages_dir / "page_1.jpg").rename(pages_dir / "page_1.png")

    resp = client.get(f"/api/v1/custom-filter-pages/{page_id}/pages/1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


# --------------------------------------------------------------------------- #
# _sweep_old_jobs_for_reference — retroactively applying a new filter page to
# jobs that already exist, the same content match (0.85 threshold, no AI) used
# for new ingestion, and the same two actions (remove a document; delete a job
# left with nothing real) performed by hand earlier this session.
# --------------------------------------------------------------------------- #

REFERENCE_TEXT = "BOILERPLATE COVER SHEET - CONFIDENTIAL - THIS TRANSMISSION IS INTENDED ONLY"
NON_MATCHING_TEXT = "Shipper: DTDS TECHNOLOGY PTE LTD, Invoice No: INV-2026-001, Total: USD 500.00"


def _make_template_document(db_session, tenant, group, name="Invoice"):
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name, doc_type=name)
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return tdoc


def _make_job_with_document(db_session, *, page_texts, reference="JOB-SWEEP", job_status="extracted"):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Test Group", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = _make_template_document(db_session, tenant, group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=job_status)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                      file_path="fake/does-not-exist.pdf", page_count=len(page_texts),
                      extracted_json={"pages": page_texts})
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    return tenant, group, job, jd


def test_sweep_removes_a_document_whose_page_matches_the_new_reference(db_session):
    # A second, non-matching document keeps the job alive, so this test can check the
    # matched document's own cleared state in isolation from the separate "delete a job left
    # with nothing real" behavior (covered on its own below).
    tenant, group, job, jd = _make_job_with_document(db_session, page_texts=[REFERENCE_TEXT])
    other_tdoc = _make_template_document(db_session, tenant, group, name="PackingList")
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=other_tdoc.id,
                               file_path="fake/real.pdf", page_count=1, file_index=1,
                               extracted_json={"pages": [NON_MATCHING_TEXT]}))
    db_session.commit()

    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path is None
    assert jd.page_count == 0


def test_sweep_leaves_a_non_matching_document_untouched(db_session):
    _, _, job, jd = _make_job_with_document(db_session, page_texts=[NON_MATCHING_TEXT])
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path == "fake/does-not-exist.pdf"


def test_sweep_deletes_a_job_left_with_nothing_real_after_removal(db_session):
    _, _, job, jd = _make_job_with_document(db_session, page_texts=[REFERENCE_TEXT])
    job_id = job.id
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    assert db_session.get(Job, job_id) is None


def test_sweep_keeps_a_job_alive_when_a_real_document_survives(db_session):
    tenant, group, job, jd_match = _make_job_with_document(
        db_session, page_texts=[REFERENCE_TEXT], reference="JOB-SWEEP-MIXED")
    other_tdoc = _make_template_document(db_session, tenant, group, name="PackingList")
    jd_real = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=other_tdoc.id,
                          file_path="fake/real.pdf", page_count=1, file_index=1,
                          extracted_json={"pages": [NON_MATCHING_TEXT]})
    db_session.add(jd_real)
    db_session.commit()
    db_session.refresh(jd_real)

    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    assert db_session.get(Job, job.id) is not None
    db_session.refresh(jd_real)
    assert jd_real.file_path == "fake/real.pdf"


def test_sweep_skips_draft_jobs(db_session):
    _, _, job, jd = _make_job_with_document(db_session, page_texts=[REFERENCE_TEXT], job_status="draft")
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path == "fake/does-not-exist.pdf"  # untouched - draft jobs are out of scope


def test_sweep_only_matches_the_given_reference_not_unrelated_text(db_session):
    # A document whose text is clearly unrelated to the reference must never match, however
    # the sweep is invoked - this is the same false-positive guard already proven for
    # classify_custom_page itself, exercised here through the sweep's own entry point.
    _, _, job, jd = _make_job_with_document(db_session, page_texts=[NON_MATCHING_TEXT])
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)
    db_session.refresh(jd)
    assert jd.file_path is not None


def test_sweep_never_deletes_a_document_where_only_some_pages_match(db_session):
    """Found live: a real 2-page document with one boilerplate cover page mixed in with one
    genuinely different, real page used to be deleted WHOLESALE the moment ANY page matched
    - destroying the real page along with the junk one. The sweep must never do that; a
    mixed document is left untouched for a human to look at instead."""
    _, _, job, jd = _make_job_with_document(
        db_session, page_texts=[REFERENCE_TEXT, NON_MATCHING_TEXT])
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path == "fake/does-not-exist.pdf"
    assert jd.page_count == 2


def test_sweep_still_removes_a_document_whose_every_page_matches(db_session):
    """The one case removal IS safe: nothing on the document is anything but the reference's
    own content, so nothing real is lost. A second, non-matching document keeps the job
    alive, so this checks the matched document's own cleared state in isolation."""
    tenant, group, job, jd = _make_job_with_document(
        db_session, page_texts=[REFERENCE_TEXT, REFERENCE_TEXT])
    other_tdoc = _make_template_document(db_session, tenant, group, name="PackingList")
    jd_real = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=other_tdoc.id,
                          file_path="fake/real.pdf", page_count=1, file_index=1,
                          extracted_json={"pages": [NON_MATCHING_TEXT]})
    db_session.add(jd_real)
    db_session.commit()

    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path is None


def test_sweep_skips_a_completed_job(db_session):
    """A completed job's ERP entry has already gone through - the real, filed customs
    record. Uploading an unrelated filter reference weeks later must never rewrite it."""
    _, _, job, jd = _make_job_with_document(
        db_session, page_texts=[REFERENCE_TEXT], job_status="completed")
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path == "fake/does-not-exist.pdf"


def test_sweep_skips_a_duplicate_job(db_session):
    """Already reviewed and settled as a duplicate - not this sweep's business either."""
    _, _, job, jd = _make_job_with_document(
        db_session, page_texts=[REFERENCE_TEXT], job_status="duplicate")
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd)
    assert jd.file_path == "fake/does-not-exist.pdf"


def test_upload_starts_the_background_sweep(client, db_session):
    _login_super_admin(client, db_session)
    with _mock_ocr("some reference text"), \
         patch("app.api.v1.custom_filter_pages._start_sweep_background") as mock_sweep:
        resp = client.post("/api/v1/custom-filter-pages?name=Trigger+Sweep",
                            files={"file": ("t.pdf", _pdf_bytes(1), "application/pdf")})
    assert resp.status_code == 201
    mock_sweep.assert_called_once_with("some reference text")

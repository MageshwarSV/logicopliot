"""GK1's IRN Documents Upload stage (arbitrary supporting files, optional, ticks the stage off
once done or explicitly skipped) and the "Prealert" - the original email a job was created
from, when it was created from one."""
import json

from app.core.job_email import job_email_dir
from app.models.job import Job
from app.models.supporting_document import SupportingDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kwargs):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-IRN", status="extracted",
             **kwargs)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _login_operator(client, db_session, tenant):
    op = make_user(db_session, role="operator", tenant=tenant, email="op-irn@example.com")
    login(client, op.email)
    return op


def _login_gk2(client, db_session, tenant, modes):
    user = make_user(db_session, role="gk2", tenant=tenant, email="gk2-irn@example.com")
    user.assigned_modes = modes
    db_session.add(user)
    db_session.commit()
    login(client, user.email)
    return user


# ---- supporting documents -----------------------------------------------------------------

def test_list_is_empty_for_a_new_job(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/supporting-documents")
    assert resp.status_code == 200, resp.text
    assert resp.json()["documents"] == []


def test_upload_a_single_file_under_a_label(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["label"] == "COO"
    assert len(body["files"]) == 1
    assert body["files"][0]["original_name"] == "coo.pdf"


def test_upload_multiple_files_under_one_label(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "Insurance"},
        files=[
            ("files", ("a.pdf", b"AAA", "application/pdf")),
            ("files", ("b.pdf", b"BBB", "application/pdf")),
        ],
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["files"]) == 2


def test_upload_requires_a_label(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "   "},
        files={"files": ("a.pdf", b"AAA", "application/pdf")},
    )
    assert resp.status_code == 422


def test_upload_no_longer_ticks_off_irn_documents_done(client, db_session):
    """GK1 must always press Approval for IRN or Skip explicitly now - uploading a document
    on its own is not enough, even though it used to be."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    assert job.irn_documents_done is False
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text

    resp2 = client.get(f"/api/v1/jobs/{job.id}")
    assert resp2.json()["irn_documents_done"] is False


def test_approve_ticks_off_irn_documents_done_and_records_the_choice(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(f"/api/v1/jobs/{job.id}/irn-documents/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["irn_documents_done"] is True
    assert resp.json()["irn_approval_requested"] is True

    resp2 = client.get(f"/api/v1/jobs/{job.id}")
    assert resp2.json()["irn_documents_done"] is True
    assert resp2.json()["irn_approval_requested"] is True


def test_skip_ticks_off_irn_documents_done_without_uploading(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(f"/api/v1/jobs/{job.id}/irn-documents/skip")
    assert resp.status_code == 200, resp.text
    assert resp.json()["irn_documents_done"] is True

    resp2 = client.get(f"/api/v1/jobs/{job.id}")
    assert resp2.json()["irn_documents_done"] is True
    assert resp2.json()["irn_approval_requested"] is False
    assert resp2.json()["documents"] is not None  # sanity: job still loads fine


def test_download_a_supporting_document_file(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    up = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"%PDF-fake-content", "application/pdf")},
    )
    doc_id = up.json()["id"]
    stored_as = up.json()["files"][0]["stored_as"]

    resp = client.get(f"/api/v1/jobs/{job.id}/supporting-documents/{doc_id}/files/{stored_as}")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-fake-content"


def test_delete_a_supporting_document(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    up = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"AAA", "application/pdf")},
    )
    doc_id = up.json()["id"]

    resp = client.delete(f"/api/v1/jobs/{job.id}/supporting-documents/{doc_id}")
    assert resp.status_code == 204, resp.text
    assert db_session.query(SupportingDocument).count() == 0


def test_deleting_the_last_supporting_document_never_touches_irn_documents_done(client, db_session):
    """irn_documents_done is only ever set by the explicit Approval-for-IRN/Skip endpoints now
    - deleting a document, even the only one, must never undo GK1's already-recorded choice."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    client.post(f"/api/v1/jobs/{job.id}/irn-documents/approve")
    up = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"AAA", "application/pdf")},
    )
    doc_id = up.json()["id"]
    assert client.get(f"/api/v1/jobs/{job.id}").json()["irn_documents_done"] is True

    resp = client.delete(f"/api/v1/jobs/{job.id}/supporting-documents/{doc_id}")
    assert resp.status_code == 204, resp.text
    assert client.get(f"/api/v1/jobs/{job.id}").json()["irn_documents_done"] is True


def test_gk2_can_also_upload_a_supporting_document(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, mode="Sea Import")
    job = _make_job(db_session, tenant, group)
    _login_gk2(client, db_session, tenant, modes=["Sea Import"])

    resp = client.post(
        f"/api/v1/jobs/{job.id}/supporting-documents",
        data={"label": "COO"},
        files={"files": ("coo.pdf", b"AAA", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text


# ---- prealert -------------------------------------------------------------------------------

def test_prealert_unavailable_for_a_job_with_no_email_behind_it(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/prealert")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"available": False}


def _write_fake_prealert(job_id: str, with_eml: bool = True):
    out = job_email_dir(job_id)
    att_dir = out / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    if with_eml:
        (out / "original.eml").write_bytes(b"From: a@b.com\r\nSubject: Hi\r\n\r\nBody")
    (att_dir / "0_invoice.pdf").write_bytes(b"INVOICE-BYTES")
    meta = {
        "sender": "shipper@example.com", "subject": "Shipment docs",
        "received_at": "2026-09-01T00:00:00+00:00",
        "attachments": [{"name": "invoice.pdf", "stored_as": "0_invoice.pdf", "size": 13}],
    }
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_prealert_metadata_when_present(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _write_fake_prealert(job.id)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/prealert")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["sender"] == "shipper@example.com"
    assert body["subject"] == "Shipment docs"
    assert body["has_original_eml"] is True
    assert body["attachments"] == [{"name": "invoice.pdf", "stored_as": "0_invoice.pdf", "size": 13}]


def test_download_prealert_original_eml(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _write_fake_prealert(job.id)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/prealert/original")
    assert resp.status_code == 200, resp.text
    assert b"Subject: Hi" in resp.content


def test_download_prealert_original_eml_404_when_none_saved(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _write_fake_prealert(job.id, with_eml=False)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/prealert/original")
    assert resp.status_code == 404


def test_download_prealert_attachment(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)
    _write_fake_prealert(job.id)
    _login_operator(client, db_session, tenant)

    resp = client.get(f"/api/v1/jobs/{job.id}/prealert/attachments/0_invoice.pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"INVOICE-BYTES"

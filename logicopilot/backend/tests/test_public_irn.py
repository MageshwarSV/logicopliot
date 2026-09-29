"""The IRN Pending link (app/api/v1/public_irn.py) - the one place on the API that accepts a
`key` query param instead of a login session, for the standalone frontend pages under
frontend/src/pages/irn/. No cookie is ever set in these tests - that IS the point being
tested: these routes must work for a client that never logged in at all.

Each TENANT has its own key (Tenant.irn_pending_access_key) rather than one key shared by
every tenant - most tests here just give ONE tenant the well-known PUBLIC_ACCESS_KEY value
(arbitrary from the key-lookup's point of view, it's just a string), and
test_a_tenants_key_never_sees_another_tenants_jobs proves the isolation that matters."""
from app.api.v1.jobs import _supporting_doc_dir
from app.api.v1.public_irn import PUBLIC_ACCESS_KEY
from app.models.job import Job
from app.models.supporting_document import SupportingDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user


def _make_tenant_with_key(db_session, key=PUBLIC_ACCESS_KEY, **kw):
    tenant = make_tenant(db_session, **kw)
    tenant.irn_pending_access_key = key
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, **kw):
    kw.setdefault("reference", "JOB-PUB")
    kw.setdefault("status", "extracted")
    job = Job(tenant_id=tenant.id, group_id=group.id, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_public_account(db_session):
    # Super admins are tenant-less by design (see app/core/deps.py's get_tenant_scope and the
    # ck_users_tenant_scope check constraint) - passing a tenant here fails that constraint.
    return make_user(db_session, role="super_admin", email="ashraf.ali@workboosterai.com")


def test_list_rejects_a_missing_key(client, db_session):
    resp = client.get("/api/v1/public/irn-pending")
    assert resp.status_code == 401


def test_list_rejects_a_wrong_key(client, db_session):
    resp = client.get("/api/v1/public/irn-pending", params={"key": "not-the-real-key"})
    assert resp.status_code == 401


def test_list_rejects_when_the_linked_account_does_not_exist(client, db_session):
    # A tenant DOES hold this key, but no ashraf.ali@workboosterai.com user is seeded at all -
    # the key resolving to a tenant is not enough on its own.
    _make_tenant_with_key(db_session)

    resp = client.get("/api/v1/public/irn-pending", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 401


def test_list_works_with_no_login_at_all_given_the_right_key(client, db_session):
    """The whole point: no cookie is set anywhere in this test."""
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    parked = _make_job(db_session, tenant, group, reference="JOB-PARKED",
                       gk2_status="irn_document_process")
    _make_job(db_session, tenant, group, reference="JOB-OTHER", gk2_status="pending")

    resp = client.get("/api/v1/public/irn-pending", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()}
    assert ids == {parked.id}


def test_list_rejects_an_inactive_linked_account(client, db_session):
    _make_tenant_with_key(db_session)
    make_user(db_session, role="super_admin",
             email="ashraf.ali@workboosterai.com", is_active=False)

    resp = client.get("/api/v1/public/irn-pending", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 401


def test_get_job_detail_with_the_right_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")

    resp = client.get(f"/api/v1/public/irn-pending/{job.id}", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == job.id


def test_get_job_detail_rejects_a_wrong_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")

    resp = client.get(f"/api/v1/public/irn-pending/{job.id}", params={"key": "wrong"})
    assert resp.status_code == 401


def test_get_job_detail_404s_for_an_unknown_job_even_with_the_right_key(client, db_session):
    _make_tenant_with_key(db_session)
    _make_public_account(db_session)

    resp = client.get("/api/v1/public/irn-pending/does-not-exist", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 404


def test_list_supporting_documents_with_the_right_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")
    db_session.add(SupportingDocument(tenant_id=tenant.id, job_id=job.id, label="COO",
                                       files=[{"stored_as": "0.pdf", "original_name": "coo.pdf", "size": 3}]))
    db_session.commit()

    resp = client.get(f"/api/v1/public/irn-pending/{job.id}/supporting-documents",
                      params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["documents"]) == 1
    assert resp.json()["documents"][0]["label"] == "COO"


def test_a_tenants_key_never_sees_another_tenants_jobs(client, db_session):
    """Each tenant has its own key now - Tenant A's key must resolve to Tenant A's jobs only,
    never Tenant B's, even though the linked account underneath (ashraf.ali) is a real Super
    Admin who could see both if scoping were dropped."""
    tenant_a = _make_tenant_with_key(db_session, key="key-for-tenant-a", name="Tenant A")
    tenant_b = _make_tenant_with_key(db_session, key="key-for-tenant-b", name="Tenant B")
    group_a = _make_group(db_session, tenant_a)
    group_b = _make_group(db_session, tenant_b)
    _make_public_account(db_session)
    job_a = _make_job(db_session, tenant_a, group_a, reference="JOB-A", gk2_status="irn_document_process")
    job_b = _make_job(db_session, tenant_b, group_b, reference="JOB-B", gk2_status="irn_document_process")

    resp = client.get("/api/v1/public/irn-pending", params={"key": "key-for-tenant-a"})
    assert resp.status_code == 200, resp.text
    assert {j["id"] for j in resp.json()} == {job_a.id}

    resp = client.get("/api/v1/public/irn-pending", params={"key": "key-for-tenant-b"})
    assert resp.status_code == 200, resp.text
    assert {j["id"] for j in resp.json()} == {job_b.id}

    # Tenant A's key must not reach Tenant B's job detail either, not just the list.
    resp = client.get(f"/api/v1/public/irn-pending/{job_b.id}", params={"key": "key-for-tenant-a"})
    assert resp.status_code == 404


def test_download_a_supporting_document_file_with_the_right_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")
    doc = SupportingDocument(tenant_id=tenant.id, job_id=job.id, label="COO",
                             files=[{"stored_as": "0.pdf", "original_name": "coo.pdf", "size": 9}])
    db_session.add(doc)
    db_session.commit()
    ddir = _supporting_doc_dir(doc.id)
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "0.pdf").write_bytes(b"%PDF-fake")

    resp = client.get(
        f"/api/v1/public/irn-pending/{job.id}/supporting-documents/{doc.id}/files/0.pdf",
        params={"key": PUBLIC_ACCESS_KEY},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-fake"


def test_download_a_supporting_document_file_rejects_a_wrong_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")
    doc = SupportingDocument(tenant_id=tenant.id, job_id=job.id, label="COO",
                             files=[{"stored_as": "0.pdf", "original_name": "coo.pdf", "size": 9}])
    db_session.add(doc)
    db_session.commit()

    resp = client.get(
        f"/api/v1/public/irn-pending/{job.id}/supporting-documents/{doc.id}/files/0.pdf",
        params={"key": "wrong"},
    )
    assert resp.status_code == 401


def _make_supporting_doc(db_session, tenant, job, label="COO"):
    doc = SupportingDocument(tenant_id=tenant.id, job_id=job.id, label=label,
                             files=[{"stored_as": "0.pdf", "original_name": f"{label}.pdf", "size": 9}])
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _sign(client, job_id, doc_ref, *, irn_number="IRN123", filename="signed.pdf"):
    return client.post(
        f"/api/v1/public/irn-pending/{job_id}/documents/{doc_ref}/sign",
        params={"key": PUBLIC_ACCESS_KEY},
        data={"irn_number": irn_number},
        files={"file": (filename, b"%PDF-signed", "application/pdf")},
    )


def test_list_documents_shows_one_unsigned_group_per_supporting_document(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)

    resp = client.get(f"/api/v1/public/irn-pending/{job.id}/documents", params={"key": PUBLIC_ACCESS_KEY})
    assert resp.status_code == 200, resp.text
    docs = resp.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["doc_ref"] == f"supporting-{doc.id}"
    assert docs[0]["label"] == "COO"
    assert docs[0]["signed"] is None


def test_sign_a_document_attaches_the_file_and_irn_number(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)

    resp = _sign(client, job.id, f"supporting-{doc.id}", irn_number=" IRN-0001 ")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["all_signed"] is True
    signed = next(d for d in body["documents"] if d["doc_ref"] == f"supporting-{doc.id}")["signed"]
    assert signed["irn_number"] == "IRN-0001"  # trimmed
    assert signed["original_name"] == "signed.pdf"


def test_sign_the_last_remaining_document_moves_the_job_to_preparing_erp(client, db_session):
    """The caller never sends a status - crossing "every header now signed" is what flips it,
    the same synchronous "preparing_erp" transition gk2_approve's own real path makes (the
    actual ERP run happens on a background thread either way, so this only checks the
    synchronous part, the same way test_gk2_approve_moves_to_preparing_erp_immediately does)."""
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc_a = _make_supporting_doc(db_session, tenant, job, label="COO")
    doc_b = _make_supporting_doc(db_session, tenant, job, label="Insurance")

    resp = _sign(client, job.id, f"supporting-{doc_a.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["all_signed"] is False
    db_session.refresh(job)
    assert job.gk2_status == "irn_document_process"

    resp = _sign(client, job.id, f"supporting-{doc_b.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["all_signed"] is True
    assert resp.json()["job_status"] == "preparing_erp"
    db_session.refresh(job)
    assert job.gk2_status == "preparing_erp"


def test_sign_rejects_a_wrong_key(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)

    resp = client.post(
        f"/api/v1/public/irn-pending/{job.id}/documents/supporting-{doc.id}/sign",
        params={"key": "wrong"},
        data={"irn_number": "IRN1"},
        files={"file": ("s.pdf", b"x", "application/pdf")},
    )
    assert resp.status_code == 401


def test_sign_rejects_an_unknown_document(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)

    resp = _sign(client, job.id, "supporting-does-not-exist")
    assert resp.status_code == 404


def test_sign_rejects_a_job_not_waiting_on_irn_signing(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="pending", irn_approval_requested=False)
    doc = _make_supporting_doc(db_session, tenant, job)

    resp = _sign(client, job.id, f"supporting-{doc.id}")
    assert resp.status_code == 409


def test_signing_again_updates_the_same_row_instead_of_duplicating(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)
    # A second, never-signed document keeps the job parked in "irn_document_process" between
    # the two sign calls below - signing the ONLY document would complete the job and move it
    # on, and re-signing a document on a job that has already moved on is correctly rejected
    # (see test_sign_rejects_a_job_not_waiting_on_irn_signing).
    _make_supporting_doc(db_session, tenant, job, label="Insurance")

    _sign(client, job.id, f"supporting-{doc.id}", irn_number="IRN-OLD", filename="old.pdf")
    resp = _sign(client, job.id, f"supporting-{doc.id}", irn_number="IRN-NEW", filename="new.pdf")
    assert resp.status_code == 200, resp.text

    from app.models.job_irn_signature import JobIrnSignature
    rows = db_session.query(JobIrnSignature).filter(JobIrnSignature.job_id == job.id).all()
    assert len(rows) == 1
    assert rows[0].irn_number == "IRN-NEW"
    assert rows[0].original_name == "new.pdf"


def test_download_the_signed_file(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)
    doc_ref = f"supporting-{doc.id}"
    _sign(client, job.id, doc_ref)

    resp = client.get(
        f"/api/v1/public/irn-pending/{job.id}/documents/{doc_ref}/signed-file",
        params={"key": PUBLIC_ACCESS_KEY},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-signed"


def test_download_the_signed_file_404s_before_anything_is_signed(client, db_session):
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process", irn_approval_requested=True)
    doc = _make_supporting_doc(db_session, tenant, job)

    resp = client.get(
        f"/api/v1/public/irn-pending/{job.id}/documents/supporting-{doc.id}/signed-file",
        params={"key": PUBLIC_ACCESS_KEY},
    )
    assert resp.status_code == 404


def test_download_a_document_page_404s_when_unrendered(client, db_session):
    """The document-page route exists and is key-gated even when there's nothing to serve yet
    (no JobDocument row at all here) - the full render pipeline is covered by the authenticated
    endpoint's own tests in test_extraction_compare_values.py and friends; this just proves the
    public route reuses the same 404 behaviour instead of a public-only shortcut."""
    tenant = _make_tenant_with_key(db_session)
    group = _make_group(db_session, tenant)
    _make_public_account(db_session)
    job = _make_job(db_session, tenant, group, gk2_status="irn_document_process")

    resp = client.get(
        f"/api/v1/public/irn-pending/{job.id}/documents/does-not-exist/pages/1",
        params={"key": PUBLIC_ACCESS_KEY},
    )
    assert resp.status_code == 404

"""PATCH /template-documents/{id}/name — the only way to fix a document's label after
creation (e.g. Air Export/Air Import shipping "INV"/"PL" instead of "Invoice"/"Packing List")
without touching every job that already used the old name."""
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_document(db_session, tenant, name="INV"):
    group = TemplateGroup(tenant_id=tenant.id, name="Air Export", status="approved", mode="Air Export")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name, doc_type="invoice")
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def test_super_admin_renames_a_document(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="INV")
    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/name", json={"name": "Invoice"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Invoice"

    db_session.refresh(doc)
    assert doc.name == "Invoice"


def test_tenant_admin_can_rename_their_own_tenants_document(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="PL")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/name", json={"name": "Packing List"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Packing List"


def test_tenant_admin_cannot_rename_another_tenants_document(client, db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    doc = _make_document(db_session, tenant_b, name="INV")
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant_a, email="ta-a@example.com")
    login(client, "ta-a@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/name", json={"name": "Invoice"})
    assert resp.status_code == 404


def test_blank_name_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="INV")
    make_user(db_session, role=SUPER_ADMIN, email="sa2@example.com")
    login(client, "sa2@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/name", json={"name": "   "})
    assert resp.status_code == 422


def test_name_is_trimmed(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="INV")
    make_user(db_session, role=SUPER_ADMIN, email="sa3@example.com")
    login(client, "sa3@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/name", json={"name": "  Invoice  "})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Invoice"


def test_super_admin_changes_a_documents_doc_type(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="Weight list")
    make_user(db_session, role=SUPER_ADMIN, email="sa4@example.com")
    login(client, "sa4@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/doc-type", json={"doc_type": "PackingList"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["doc_type"] == "PackingList"

    db_session.refresh(doc)
    assert doc.doc_type == "PackingList"


def test_doc_type_change_is_reflected_on_an_already_extracted_job(client, db_session):
    """doc_type is never copied onto JobDocument - it's read live off the TemplateDocument
    every time a job is serialized - so fixing it here must fix it for every job that
    already used this slot too, with no separate per-job update needed."""
    from app.models.job import Job, JobDocument

    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="Weight list")
    job = Job(tenant_id=tenant.id, group_id=doc.group_id, reference="JOB-OLD", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=doc.id,
                     file_path="fake.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa5@example.com")
    login(client, "sa5@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/doc-type", json={"doc_type": "PackingList"})
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    job_doc = next(d for d in resp.json()["documents"] if d["template_document_id"] == doc.id)
    assert job_doc["doc_type"] == "PackingList"


def test_blank_doc_type_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    doc = _make_document(db_session, tenant, name="Weight list")
    make_user(db_session, role=SUPER_ADMIN, email="sa6@example.com")
    login(client, "sa6@example.com")

    resp = client.patch(f"/api/v1/template-documents/{doc.id}/doc-type", json={"doc_type": "   "})
    assert resp.status_code == 422

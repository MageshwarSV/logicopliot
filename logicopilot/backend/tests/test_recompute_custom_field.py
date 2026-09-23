"""POST /jobs/{job_id}/custom-fields/{custom_field_id}/recompute — backfilling one
AI-computed custom field on an already-extracted job, added to the template AFTER that job
was extracted, without re-running extraction (which would rewrite every other field and
reset every approval)."""
from unittest.mock import patch

from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return group, tdoc


def _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-BACKFILL", cached_text=None):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted",
             validation_approved=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1,
                     extracted_json=({"text": cached_text, "pages": [cached_text]} if cached_text is not None else None))
    db_session.add(jd)
    db_session.commit()
    return job


def test_backfills_from_already_cached_ocr_text(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc,
                              cached_text="Consignee: NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                     kind="ai", ai_prompt="Read the consignee off the Bill of Lading.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa@example.com")
    login(client, "sa@example.com")

    with patch("app.core.llm.compute_custom_field", return_value="NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD") as mock_compute:
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["extracted_value"] == "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD"
    assert resp.json()["is_custom"] is True
    assert resp.json()["origin"] == "computed"

    # The prompt actually reached the mocked call, built from the CACHED text - no OCR call.
    docs_text_arg = mock_compute.call_args.args[1]
    assert "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD" in docs_text_arg


def test_second_call_updates_the_same_row_not_a_new_one(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc, cached_text="Consignee: Old Reading")
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                     kind="ai", ai_prompt="Read the consignee.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa2@example.com")
    login(client, "sa2@example.com")

    with patch("app.core.llm.compute_custom_field", return_value="First Value"):
        client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    with patch("app.core.llm.compute_custom_field", return_value="Second Value"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")

    assert resp.status_code == 200
    assert resp.json()["extracted_value"] == "Second Value"
    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].extracted_value == "Second Value"


def test_nothing_else_on_the_job_is_touched(client, db_session):
    """The whole point: status, approvals, and every OTHER field value survive untouched -
    unlike /extract, which would rewrite all of it."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc, cached_text="Consignee: Someone")
    other_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Untouched Field",
                           kind="hardcoded", hardcoded_value="stays exactly as is")
    consignee_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                               kind="ai", ai_prompt="Read the consignee.")
    db_session.add_all([other_cf, consignee_cf])
    db_session.commit()
    db_session.refresh(other_cf)
    db_session.refresh(consignee_cf)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=other_cf.id,
                                 label_name="Untouched Field", extracted_value="stays exactly as is"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa3@example.com")
    login(client, "sa3@example.com")

    with patch("app.core.llm.compute_custom_field", return_value="Real Consignee Co"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{consignee_cf.id}/recompute")
    assert resp.status_code == 200

    db_session.refresh(job)
    assert job.status == "extracted"
    assert job.validation_approved is True
    other_value = (db_session.query(JobFieldValue)
                   .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == other_cf.id)
                   .first())
    assert other_value.extracted_value == "stays exactly as is"


def test_renaming_the_field_after_a_row_exists_updates_the_label_on_recompute(client, db_session):
    """A field renamed (e.g. via the wizard's edit-field screen) after a job was already
    extracted must not keep showing the operator its old name forever - found live: the
    existing JobFieldValue row's label_name was never re-synced on recompute, only set once at
    row creation."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc, cached_text="Consignee: Someone")
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Old Name",
                     kind="ai", ai_prompt="Read the consignee.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-rename@example.com")
    login(client, "sa-rename@example.com")

    with patch("app.core.llm.compute_custom_field", return_value="Some Value"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.json()["label_name"] == "Old Name"

    cf.label_name = "New Name"
    db_session.commit()

    with patch("app.core.llm.compute_custom_field", return_value="Some Value"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()["label_name"] == "New Name"
    row = (db_session.query(JobFieldValue)
           .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).one())
    assert row.label_name == "New Name"


def test_rejects_a_hardcoded_field(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Fixed Thing",
                     kind="hardcoded", hardcoded_value="X")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa4@example.com")
    login(client, "sa4@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 400


def test_rejects_a_custom_field_from_a_different_template(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    other_group = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="approved", mode="Sea Export")
    db_session.add(other_group)
    db_session.commit()
    db_session.refresh(other_group)
    foreign_cf = CustomField(tenant_id=tenant.id, group_id=other_group.id, label_name="Elsewhere",
                             kind="ai", ai_prompt="...")
    db_session.add(foreign_cf)
    db_session.commit()
    db_session.refresh(foreign_cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa5@example.com")
    login(client, "sa5@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{foreign_cf.id}/recompute")
    assert resp.status_code == 404


def test_tenant_admin_cannot_call_it(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                     kind="ai", ai_prompt="...")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta@example.com")
    login(client, "ta@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 403


def test_falls_back_to_ocr_when_nothing_cached_yet(client, db_session):
    """An old job extracted before extracted_json existed as a feature - the fallback path
    still has to work, calling get_page_ocr the same way a real extraction would."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc, cached_text=None)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                     kind="ai", ai_prompt="Read the consignee.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa6@example.com")
    login(client, "sa6@example.com")

    with patch("app.api.v1.jobs.get_page_ocr",
               return_value={"layout_text": "Consignee: Freshly Read Co", "text": "Consignee: Freshly Read Co", "tokens": []}), \
         patch("app.core.llm.compute_custom_field", return_value="Freshly Read Co") as mock_compute:
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")

    assert resp.status_code == 200, resp.text
    assert resp.json()["extracted_value"] == "Freshly Read Co"
    docs_text_arg = mock_compute.call_args.args[1]
    assert "Freshly Read Co" in docs_text_arg

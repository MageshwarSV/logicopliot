"""POST /jobs/{job_id}/dedupe-field-values — surgically removing the duplicate JobFieldValue
rows the _maybe_auto_extract race used to leave behind (see its own fix), without the risk of
a full re-extraction, which would also wipe any correction already typed on top of a
duplicate."""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def test_removes_the_extra_row_of_a_per_row_custom_field_duplicate(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC",
                     kind="hardcoded", hardcoded_value="011/2021", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=1),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe1@example.com")
    login(client, "sa-dedupe1@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].extracted_value == "011/2021"


def test_keeps_the_row_with_a_correction_over_the_one_without(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Product CTH no",
                     kind="lookup", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE2", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    uncorrected = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                label_name="Product CTH no", extracted_value="", set_index=1, row_index=1)
    corrected = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                              label_name="Product CTH no", extracted_value="",
                              corrected_value="87089900", set_index=1, row_index=1)
    db_session.add_all([uncorrected, corrected])
    db_session.commit()
    db_session.refresh(corrected)

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe2@example.com")
    login(client, "sa-dedupe2@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].id == corrected.id
    assert rows[0].corrected_value == "87089900"


def test_removes_the_extra_row_of_a_duplicated_mark(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="CountryOfShipmentCode",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE3", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                     label_name="CountryOfShipmentCode", extracted_value="HK"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                     label_name="CountryOfShipmentCode", extracted_value="HK"),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe3@example.com")
    login(client, "sa-dedupe3@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1
    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.mark_id == mark.id).all())
    assert len(rows) == 1


def test_a_second_call_does_nothing_more(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="IGST Duty",
                     kind="hardcoded", hardcoded_value="009/2025", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE4", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="IGST Duty", extracted_value="009/2025", set_index=1, row_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="IGST Duty", extracted_value="009/2025", set_index=1, row_index=1),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe4@example.com")
    login(client, "sa-dedupe4@example.com")

    first = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert first.json()["removed"] == 1
    second = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert second.json()["removed"] == 0


def test_untouched_when_nothing_is_duplicated(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC",
                     kind="hardcoded", hardcoded_value="011/2021", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE5", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=2),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe5@example.com")
    login(client, "sa-dedupe5@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 0
    assert (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).count()) == 2


def test_folds_an_orphaned_set_index_onto_the_jobs_one_real_set(client, db_session):
    """The exact live straggler case: recompute-doc-sets already unified every real document
    (which DOES carry a job_document_id) onto set 1, but a per-row custom field value has no
    job_document_id at all, so that endpoint never touched it - it is still labelled set 2,
    a set nothing else on the job carries anymore. Must still be recognised as the same
    duplicate its set-1 sibling always was."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC",
                     kind="hardcoded", hardcoded_value="011/2021", per_row=True)
    db_session.add_all([mark, cf])
    db_session.commit()
    db_session.refresh(mark)
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE7", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # The job's only real document, correctly unified onto set 1 by an earlier
    # recompute-doc-sets call (job_document_id set, matching what that endpoint stamps).
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                                 template_document_id=tdoc.id, job_document_id="fake-jd-1",
                                 label_name="Invoice No", extracted_value="E26000505", set_index=1))
    # The per-row custom field's own duplicate pair - one on the real set, one still an
    # orphan of the old numbering (no job_document_id, since per-row custom field values
    # never carry one).
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=2, row_index=1),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe7@example.com")
    login(client, "sa-dedupe7@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 1

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].set_index == 1


def test_does_not_fold_across_two_genuinely_different_real_sets(client, db_session):
    """A job with two REAL invoices (two documents each carrying their own job_document_id,
    two different set numbers) must not have its per-row custom field values collapsed onto
    one set - that would actually merge two different products' data."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC",
                     kind="hardcoded", hardcoded_value="011/2021", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE8", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, template_document_id=tdoc.id,
                     job_document_id="fake-jd-1", label_name="Invoice No", extracted_value="E1", set_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, template_document_id=tdoc.id,
                     job_document_id="fake-jd-2", label_name="Invoice No", extracted_value="E2", set_index=2),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=1, row_index=1),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                     label_name="AIDC", extracted_value="011/2021", set_index=2, row_index=1),
    ])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-dedupe8@example.com")
    login(client, "sa-dedupe8@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] == 0
    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert {r.set_index for r in rows} == {1, 2}


def test_tenant_admin_cannot_call_it(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DEDUPE6", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-dedupe@example.com")
    login(client, "ta-dedupe@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/dedupe-field-values")
    assert resp.status_code == 403

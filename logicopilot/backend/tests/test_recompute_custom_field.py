"""POST /jobs/{job_id}/custom-fields/{custom_field_id}/recompute — backfilling one
AI-computed or hardcoded custom field on an already-extracted job, added to the template
AFTER that job was extracted, without re-running extraction (which would rewrite every
other field and reset every approval)."""
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
    assert resp.json()[0]["extracted_value"] == "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD"
    assert resp.json()[0]["is_custom"] is True
    assert resp.json()[0]["origin"] == "computed"

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
    assert resp.json()[0]["extracted_value"] == "Second Value"
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
    assert resp.json()[0]["label_name"] == "Old Name"

    cf.label_name = "New Name"
    db_session.commit()

    with patch("app.core.llm.compute_custom_field", return_value="Some Value"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["label_name"] == "New Name"
    row = (db_session.query(JobFieldValue)
           .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).one())
    assert row.label_name == "New Name"


def test_backfills_a_hardcoded_field_from_its_own_value(client, db_session):
    """A hardcoded field needs no document at all - the value IS its own hardcoded_value.
    This is exactly how a "Manual Entry" field created straight from the ERP Script
    Recorder shows up on a job that was already extracted before that field existed: it
    has ask_operator on and no hardcoded_value, so this backfill leaves it correctly blank
    (still asked for on Additional Details), rather than 400ing and leaving no way to get
    it onto the job short of a full re-extraction."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Fixed Thing",
                     kind="hardcoded", hardcoded_value="X")
    manual_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Erp Entry Name",
                            kind="hardcoded", hardcoded_value=None, ask_operator=True,
                            ask_operator_required=True)
    db_session.add_all([cf, manual_cf])
    db_session.commit()
    db_session.refresh(cf)
    db_session.refresh(manual_cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa4@example.com")
    login(client, "sa4@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["extracted_value"] == "X"

    resp2 = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{manual_cf.id}/recompute")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()[0]["extracted_value"] == ""
    row = (db_session.query(JobFieldValue)
           .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == manual_cf.id)
           .one())
    assert row.label_name == "Erp Entry Name"


def test_target_value_field_keyed_off_a_different_field(client, db_session):
    """"Erp Entry Name" has nothing of its own to extract (kind="hardcoded", no fixed
    value) - it is keyed entirely off "Quotation Name", a separate field on the same job,
    via lookup_key_label. No reference learned yet -> empty, exactly the same "still asked
    for on Additional Details" outcome a plain hardcoded field with no value gets. Once an
    operator's correction is learned (via PATCH /job-field-values/{id}, the normal
    Additional Details save path), the SAME quotation name - even spelled differently,
    since fuzzy_match is on - resolves automatically on a later job without asking again."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    manual_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Erp Entry Name",
                            kind="hardcoded", hardcoded_value=None, ask_operator=True,
                            ask_operator_required=True, is_target_value=True, fuzzy_match=True,
                            lookup_key_label="Quotation Name")
    db_session.add(manual_cf)
    db_session.commit()
    db_session.refresh(manual_cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa4c@example.com")
    login(client, "sa4c@example.com")

    # Job 1: has a Quotation Name, nothing learned for it yet.
    job1 = _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-TV1")
    qn1 = JobFieldValue(tenant_id=tenant.id, job_id=job1.id, label_name="Quotation Name",
                        extracted_value="KUEHNE + NAGEL PVT. LTD.")
    db_session.add(qn1)
    db_session.commit()

    resp1 = client.post(f"/api/v1/jobs/{job1.id}/custom-fields/{manual_cf.id}/recompute")
    assert resp1.status_code == 200, resp1.text
    assert not resp1.json()[0]["extracted_value"]
    row1 = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job1.id, JobFieldValue.custom_field_id == manual_cf.id)
            .one())
    assert row1.target_value_raw == "KUEHNE + NAGEL PVT. LTD."

    # The operator answers it once - the normal Additional Details save path.
    resp_correct = client.patch(f"/api/v1/job-field-values/{row1.id}",
                                json={"corrected_value": "KUEHNE+NAGEL PVT LTD"})
    assert resp_correct.status_code == 200, resp_correct.text

    # Job 2: the SAME quotation name, spelled differently (OCR/punctuation noise) - fuzzy
    # match finds job 1's learned answer without asking again.
    job2 = _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-TV2")
    qn2 = JobFieldValue(tenant_id=tenant.id, job_id=job2.id, label_name="Quotation Name",
                        extracted_value="Kuehne & Nagel Pvt Ltd")
    db_session.add(qn2)
    db_session.commit()

    resp2 = client.post(f"/api/v1/jobs/{job2.id}/custom-fields/{manual_cf.id}/recompute")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()[0]["extracted_value"] == "KUEHNE+NAGEL PVT LTD"


def test_rejects_a_lookup_field(client, db_session):
    """A per-row lookup field (the CTH/HS code) is keyed on another field of the same LINE
    and has no meaning at job level - it is not something this endpoint (job-level,
    row_index=None) can backfill."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH Code",
                     kind="lookup", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa4b@example.com")
    login(client, "sa4b@example.com")

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


def test_per_row_field_backfills_one_value_per_existing_line(client, db_session):
    """A field switched to per_row AFTER a job was already extracted still had only its one
    old job-level slot. Recompute should create one JobFieldValue per (set_index, row_index)
    the job's own line-item marks already established, using the field's hardcoded_value for
    every line - the same fallback run_extraction's own per-row loop uses for a non-lookup
    field."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    # Two invoice sets' worth of line items already on the job, from marks extracted before
    # this field existed.
    for set_index, row_index in [(1, 1), (1, 2), (2, 1)]:
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                     label_name="item_material_code", extracted_value="X",
                                     set_index=set_index, row_index=row_index))
    db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC_LevyNotnSrNo",
                     kind="hardcoded", hardcoded_value="17", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-perrow@example.com")
    login(client, "sa-perrow@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 3
    assert all(r["extracted_value"] == "17" for r in rows)
    assert sorted((r["set_index"], r["row_index"]) for r in rows) == [(1, 1), (1, 2), (2, 1)]

    db_values = (db_session.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(db_values) == 3
    assert all(v.row_index is not None for v in db_values)


def test_per_row_field_second_call_updates_the_same_rows_not_new_ones(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                 label_name="item_material_code", extracted_value="X",
                                 set_index=1, row_index=1))
    db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Basic_NotnSrNo",
                     kind="hardcoded", hardcoded_value=None, per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-perrow2@example.com")
    login(client, "sa-perrow2@example.com")

    client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    cf.hardcoded_value = "56"
    db_session.commit()
    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["extracted_value"] == "56"

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].extracted_value == "56"


def test_per_row_field_drops_a_stale_job_level_row_left_from_before_the_switch(client, db_session):
    """A field flipped to per_row AFTER it already had a single job-level answer must not
    leave that old row_index=None row behind - the operator would see the same field twice,
    once in "For the whole job" and once per product line."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                 label_name="item_material_code", extracted_value="X",
                                 set_index=1, row_index=1))
    db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC_LevyNotnSrNo",
                     kind="hardcoded", hardcoded_value="17", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    # The stale job-level row from before this field became per_row.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name="AIDC_LevyNotnSrNo", extracted_value="17"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-perrow4@example.com")
    login(client, "sa-perrow4@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 1
    assert rows[0].row_index == 1


def test_per_row_field_keeps_a_stale_job_level_row_if_it_was_actually_corrected(client, db_session):
    """A real operator correction on the old job-level row is never silently dropped - there
    is no single line it can be automatically reassigned to."""
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                 label_name="item_material_code", extracted_value="X",
                                 set_index=1, row_index=1))
    db_session.commit()
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC_LevyNotnSrNo",
                     kind="hardcoded", hardcoded_value="17", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                                 label_name="AIDC_LevyNotnSrNo", extracted_value="17",
                                 corrected_value="19"))
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-perrow5@example.com")
    login(client, "sa-perrow5@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 200, resp.text

    rows = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all())
    assert len(rows) == 2
    stale = next(r for r in rows if r.row_index is None)
    assert stale.corrected_value == "19"


def test_per_row_field_with_no_line_items_yet_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AIDC_LevyNotnSrNo",
                     kind="hardcoded", hardcoded_value="17", per_row=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-perrow3@example.com")
    login(client, "sa-perrow3@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")
    assert resp.status_code == 400


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
    assert resp.json()[0]["extracted_value"] == "Freshly Read Co"
    docs_text_arg = mock_compute.call_args.args[1]
    assert "Freshly Read Co" in docs_text_arg

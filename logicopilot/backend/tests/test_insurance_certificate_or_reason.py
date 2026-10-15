"""Insurance Rate (%)'s own two small pieces:

1. suggest_custom_field_value: a starting suggestion for a plain ask_operator field, read off
   another field on the SAME job (e.g. Consignee) against this field's own reference table -
   fuzzy-matched, since a customer's name rarely prints identically twice.
2. upload_supporting_document_or_reason: exactly one of an uploaded file or a typed reason,
   saved as an ordinary SupportingDocument so it shows up on IRN Processing's own document list.
3. correct_field_value's own "remember insurance_percentage keyed on this job's Consignee"
   branch, so a customer's rate - once typed for one job - is suggested automatically on the
   next one instead of being retyped from scratch.
"""
from app.core.reference_cache import remember_reference
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.supporting_document import SupportingDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_insurance_field(db_session, tenant, group):
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="insurance_percentage",
        kind="hardcoded", ask_operator=True, ask_operator_required=False, fuzzy_match=True,
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return cf


def _make_job(db_session, tenant, group, **kwargs):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-INS001", status="extracted",
             **kwargs)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _set_consignee(db_session, job, tenant, value):
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="Consignee",
                        extracted_value=value)
    db_session.add(fv)
    db_session.commit()
    return fv


def _login_operator(client, db_session, tenant):
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ins@example.com")
    login(client, op.email)
    return op


# ---- suggest_custom_field_value -------------------------------------------------------------

def test_suggestion_is_none_with_nothing_learned(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _set_consignee(db_session, job, tenant, "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")
    _login_operator(client, db_session, tenant)

    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/suggested-value",
        params={"keyed_on_label": "Consignee"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"value": None}


def test_suggestion_fuzzy_matches_a_differently_printed_name(client, db_session):
    """The whole point: the same customer's name rarely prints identically twice, but a
    distinctive word (NOKIA) is always there - fuzzy_match=True (substring/near-match) is what
    lets a short or differently-worded printing still resolve to the learned rate."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    remember_reference(
        db_session, cf.id, ["NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED"],
        "0.00881715", fuzzy=True,
    )
    db_session.commit()
    job = _make_job(db_session, tenant, group)
    # Printed slightly differently on THIS job's invoice (PVT LTD vs PRIVATE LIMITED).
    _set_consignee(db_session, job, tenant, "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")
    _login_operator(client, db_session, tenant)

    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/suggested-value",
        params={"keyed_on_label": "Consignee"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"value": "0.00881715"}


def test_suggestion_matches_a_short_distinctive_key(client, db_session):
    """"YUZHAN" alone (no legal suffix at all) must still match the full printed name - a
    substring either direction counts as the same real-world company (_fuzzy_similar)."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    remember_reference(db_session, cf.id, ["YUZHAN"], "1.125", fuzzy=True)
    db_session.commit()
    job = _make_job(db_session, tenant, group)
    _set_consignee(db_session, job, tenant, "YUZHAN TECHNOLOGY (INDIA) PVT.LTD")
    _login_operator(client, db_session, tenant)

    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/suggested-value",
        params={"keyed_on_label": "Consignee"},
    )
    assert resp.json() == {"value": "1.125"}


def test_suggestion_is_none_when_the_keyed_on_field_is_blank(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    remember_reference(db_session, cf.id, ["NOKIA"], "0.00881715", fuzzy=True)
    db_session.commit()
    job = _make_job(db_session, tenant, group)  # no Consignee value set at all
    _login_operator(client, db_session, tenant)

    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/suggested-value",
        params={"keyed_on_label": "Consignee"},
    )
    assert resp.json() == {"value": None}


# ---- correct_field_value never writes back to Insurance Rate's reference sheet ----------------

def test_correcting_insurance_rate_never_touches_its_reference_sheet(client, db_session):
    """The reference sheet (Consignee -> rate) is a FIXED lookup, never self-learning - unlike
    "Erp Entry Name" and every other plain ask_operator field, which genuinely do learn from
    what gets typed. An operator picking a DIFFERENT customer's rate for one odd Nokia job (or
    typing an outlier) must never rewrite what every FUTURE Nokia job gets suggested."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    remember_reference(
        db_session, cf.id, ["NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED"],
        "0.00881715", fuzzy=True,
    )
    db_session.commit()
    job = _make_job(db_session, tenant, group)
    _set_consignee(db_session, job, tenant, "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="insurance_percentage",
                       custom_field_id=cf.id, extracted_value="")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    _login_operator(client, db_session, tenant)

    # Operator overrides the suggestion with a different customer's rate, for this one job only.
    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "1.125"})
    assert resp.status_code == 200, resp.text

    # The next Nokia job must still suggest Nokia's own, UNCHANGED rate.
    job2 = _make_job(db_session, tenant, group)
    _set_consignee(db_session, job2, tenant, "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD")
    suggestion = client.get(
        f"/api/v1/jobs/{job2.id}/custom-fields/{cf.id}/suggested-value",
        params={"keyed_on_label": "Consignee"},
    )
    assert suggestion.json() == {"value": "0.00881715"}


def test_correcting_insurance_rate_with_no_consignee_remembers_nothing(client, db_session):
    """Nothing to key on at all - must not crash, and (same as above) must never write back to
    the reference sheet either way."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)  # no Consignee set
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="insurance_percentage",
                       custom_field_id=cf.id, extracted_value="")
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)
    _login_operator(client, db_session, tenant)

    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "2.5"})
    assert resp.status_code == 200, resp.text

    from app.models.custom_field_reference import CustomFieldReferenceValue
    assert db_session.query(CustomFieldReferenceValue).filter(
        CustomFieldReferenceValue.custom_field_id == cf.id).count() == 0


# ---- upload_supporting_document_or_reason -----------------------------------------------------

def test_upload_a_certificate_file(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/supporting-document-or-reason",
        data={"label": "Insurance Premium Certificate"},
        files={"files": ("premium.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["files"]) == 1
    assert body["reason_text"] is None


def test_give_a_reason_instead_of_uploading(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/supporting-document-or-reason",
        data={"label": "Insurance Premium Certificate", "reason": "Covered under an annual blanket policy"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["files"] == []
    assert body["reason_text"] == "Covered under an annual blanket policy"


def test_both_a_file_and_a_reason_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/supporting-document-or-reason",
        data={"label": "Insurance Premium Certificate", "reason": "No certificate yet"},
        files={"files": ("premium.pdf", b"%PDF-fake", "application/pdf")},
    )
    assert resp.status_code == 422


def test_neither_a_file_nor_a_reason_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/supporting-document-or-reason",
        data={"label": "Insurance Premium Certificate"},
    )
    assert resp.status_code == 422


def test_saved_as_an_ordinary_supporting_document_visible_on_the_jobs_list(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    cf = _make_insurance_field(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)
    _login_operator(client, db_session, tenant)

    client.post(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/supporting-document-or-reason",
        data={"label": "Insurance Premium Certificate", "reason": "No certificate yet"},
    )
    assert db_session.query(SupportingDocument).filter(
        SupportingDocument.job_id == job.id).count() == 1

    listing = client.get(f"/api/v1/jobs/{job.id}/supporting-documents")
    assert listing.status_code == 200, listing.text
    docs = listing.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["label"] == "Insurance Premium Certificate"
    assert docs[0]["reason_text"] == "No certificate yet"

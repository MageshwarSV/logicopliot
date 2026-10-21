"""GET /jobs/{job_id}/custom-fields/{custom_field_id}/reference-search - the manual fallback
for a reference-backed field too large to ever offer as a plain dropdown (e.g. Shipment Port
Code's ~72,000-row world port table). When the automatic lookup (exact, then AI-assisted)
finds nothing, the operator searches this table themselves and picks the right row by
inspecting the actual document. Any role that can open the job can call this.
"""
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _setup(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REFSEARCH", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Shipment Port Code",
        kind="hardcoded", is_target_value=True, fuzzy_match=True,
        lookup_key_label="Shipment Country,Shipment City",
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add_all([
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="CHINA",
                                  match_value_2="SHANGHAI", resolved_value="CNSHA"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="CHINA",
                                  match_value_2="PUDONG/SHANGHAI", resolved_value="CNPDG"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="THAILAND",
                                  match_value_2="LAEM CHABANG", resolved_value="THLCH"),
    ])
    db_session.commit()
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refsearch@example.com")
    return tenant, group, job, cf, operator


def test_matches_a_substring_of_the_place_name_case_insensitively(client, db_session):
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=shang")
    assert resp.status_code == 200, resp.text
    values = {r["value"] for r in resp.json()["results"]}
    assert values == {"CNSHA", "CNPDG"}


def test_result_label_is_country_city_code(client, db_session):
    """'China Shanghai(CNSHA)' - lets the operator match a result against whatever they
    recognise on the actual document, country or city, without hunting for the code alone."""
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=shang")
    assert resp.status_code == 200, resp.text
    labels = {r["value"]: r["label"] for r in resp.json()["results"]}
    assert labels["CNSHA"] == "CHINA SHANGHAI(CNSHA)"
    assert labels["CNPDG"] == "CHINA PUDONG/SHANGHAI(CNPDG)"


def test_result_label_is_plain_resolved_value_when_there_is_only_one_key(client, db_session):
    """A field with no second key at all (e.g. ERP Consignee Name - one slot, match_value_1 IS
    the normalized organization name) has nothing useful to add in parentheses: match_value_1
    and resolved_value are the same name, just differently cased, so showing both read as a
    redundant "ACME CORP (Acme Corp)". Found live: this also made the dropdown unreadable for
    long organization names, since the redundant half pushed the real name further toward
    truncation. Plain resolved_value alone for this shape - it carries the original casing."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REFSEARCH2", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ERP Consignee Name",
                     kind="hardcoded", is_target_value=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add(CustomFieldReferenceValue(
        custom_field_id=cf.id, match_value_1="ACME CORP", resolved_value="Acme Corp"))
    db_session.commit()

    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refsearch2@example.com")
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=acme")
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results == [{"value": "Acme Corp", "label": "Acme Corp"}]


def test_matches_a_substring_of_the_country_too(client, db_session):
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=thai")
    assert resp.status_code == 200, resp.text
    assert [r["value"] for r in resp.json()["results"]] == ["THLCH"]


def test_a_query_under_two_characters_returns_nothing(client, db_session):
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=s")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"results": []}


def test_results_are_capped(client, db_session, monkeypatch):
    tenant, group, job, cf, operator = _setup(db_session)
    import app.api.v1.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "REFERENCE_SEARCH_RESULT_LIMIT", 1)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=shang")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["results"]) == 1


def test_a_custom_field_from_a_different_template_group_is_not_found(client, db_session):
    tenant, group, job, cf, operator = _setup(db_session)
    other_group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved")
    db_session.add(other_group)
    db_session.commit()
    db_session.refresh(other_group)
    foreign_cf = CustomField(
        tenant_id=tenant.id, group_id=other_group.id, label_name="Other Field", kind="hardcoded",
    )
    db_session.add(foreign_cf)
    db_session.commit()
    db_session.refresh(foreign_cf)

    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{foreign_cf.id}/reference-search?q=shang")
    assert resp.status_code == 404

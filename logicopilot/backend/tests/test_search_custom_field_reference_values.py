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
    # value/label specifically, not whole-dict equality: a result also carries the two keys
    # it was matched on (match_1/match_2), which this shape has nothing useful in.
    assert [(r["value"], r["label"]) for r in results] == [("Acme Corp", "Acme Corp")]


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


def _setup_consignee_with_countries(db_session):
    """ERP Consignee Name's new shape: match_value_1 is the organization, match_value_2 is now
    a COUNTRY rather than blank - and the same organization can legitimately repeat under more
    than one country."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REFSEARCH3", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ERP Consignee Name",
                     kind="hardcoded", is_target_value=True)
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    db_session.add_all([
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="JABIL CIRCUIT",
                                  match_value_2="CHINA", resolved_value="Jabil Circuit (China)"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="JABIL CIRCUIT",
                                  match_value_2="HUNGARY", resolved_value="Jabil Circuit (Hungary)"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="ACME CORP",
                                  match_value_2="INDIA", resolved_value="Acme Corp"),
    ])
    db_session.commit()
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refsearch3@example.com")
    return tenant, group, job, cf, operator


def test_no_country_param_at_all_behaves_exactly_as_before(client, db_session):
    """Shipment Port Code (and any other caller that never sends `country`) must see zero
    behavior change - the whole point of making this opt-in."""
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=shang")
    assert resp.status_code == 200, resp.text
    values = {r["value"] for r in resp.json()["results"]}
    assert values == {"CNSHA", "CNPDG"}


def test_country_param_scopes_the_match_to_that_country_only(client, db_session):
    tenant, group, job, cf, operator = _setup_consignee_with_countries(db_session)
    login(client, operator.email)
    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=jabil&country=china"
    )
    assert resp.status_code == 200, resp.text
    assert [r["value"] for r in resp.json()["results"]] == ["Jabil Circuit (China)"]


def test_country_param_present_but_empty_returns_nothing_rather_than_every_country(client, db_session):
    tenant, group, job, cf, operator = _setup_consignee_with_countries(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=jabil&country=")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"results": []}


def test_country_scoped_result_label_is_plain_resolved_value_even_with_a_second_key(client, db_session):
    """Country-scoped results never show the country in the label (it's already known from the
    dropdown that picked it) - unlike Shipment Port Code's own "Country City(CODE)" shape."""
    tenant, group, job, cf, operator = _setup_consignee_with_countries(db_session)
    login(client, operator.email)
    resp = client.get(
        f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=jabil&country=china"
    )
    assert resp.status_code == 200, resp.text
    # value/label specifically, not whole-dict equality - see the note on the one-key test.
    assert [(r["value"], r["label"]) for r in resp.json()["results"]] == [
        ("Jabil Circuit (China)", "Jabil Circuit (China)")
    ]


def test_reference_countries_endpoint_lists_every_distinct_country(client, db_session):
    tenant, group, job, cf, operator = _setup_consignee_with_countries(db_session)
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-countries")
    assert resp.status_code == 200, resp.text
    assert resp.json()["countries"] == ["CHINA", "HUNGARY", "INDIA"]


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


def test_each_result_carries_the_two_keys_it_was_matched_on(client, db_session):
    """Picking a port has already told the operator the country and the city - they are two
    columns of the very row that produced the code. They come back with it so the screen can
    fill Shipment Country / Shipment City from the same row (REFERENCE_PICK_FILLS_FIELDS in
    JobRunPage.tsx) instead of making the operator retype both off the same document.

    Raw and normalized (ALL CAPS), deliberately: what the two keys MEAN differs per field -
    (country, place) here, (organization, country) on ERP Consignee Name - so the caller that
    knows which field it searched owns both the naming and the casing."""
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=laem")
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["value"] == "THLCH"
    assert results[0]["match_1"] == "THAILAND"
    assert results[0]["match_2"] == "LAEM CHABANG"


def test_the_keys_come_from_the_picked_row_not_the_query(client, db_session):
    """Two rows can match one query while describing DIFFERENT places - "shang" finds both
    Shanghai and Pudong/Shanghai. Each result has to carry its own row's keys, or picking the
    second would fill the city from the first."""
    tenant, group, job, cf, operator = _setup(db_session)
    login(client, operator.email)

    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-search?q=shang")
    assert resp.status_code == 200, resp.text
    by_value = {r["value"]: r for r in resp.json()["results"]}
    assert by_value["CNSHA"]["match_2"] == "SHANGHAI"
    assert by_value["CNPDG"]["match_2"] == "PUDONG/SHANGHAI"
    assert by_value["CNSHA"]["match_1"] == by_value["CNPDG"]["match_1"] == "CHINA"

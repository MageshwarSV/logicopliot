"""GET /jobs/{job_id}/custom-fields/{custom_field_id}/reference-options - lets Additional
Details offer a dropdown of every valid resolved_value a reference-backed field carries (e.g.
every real Customs House Code), rather than only ever showing the one value an automatic
lookup matched. Any role that can open the job can call this - it names only resolved_value,
never the table's own match columns.
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
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-REFOPTS", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="CustomsHouseCode",
        kind="hardcoded", hardcoded_value=None, is_target_value=True,
        lookup_key_label="port_of_entry,TransportModeCode",
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return tenant, group, job, cf


def test_lists_every_distinct_resolved_value_sorted(client, db_session):
    tenant, group, job, cf = _setup(db_session)
    db_session.add_all([
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="CHENNAI", match_value_2="A",
                                  resolved_value="INMAA4"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="CHENNAI", match_value_2="S",
                                  resolved_value="INMAA1"),
        CustomFieldReferenceValue(custom_field_id=cf.id, match_value_1="MUNDRA", match_value_2="S",
                                  resolved_value="INMUN1"),
    ])
    db_session.commit()

    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refopts@example.com")
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-options")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"options": ["INMAA1", "INMAA4", "INMUN1"]}


def test_a_field_with_no_reference_rows_yet_returns_an_empty_list(client, db_session):
    tenant, group, job, cf = _setup(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refopts-empty@example.com")
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/reference-options")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"options": []}


def test_a_custom_field_from_a_different_template_group_is_not_found(client, db_session):
    tenant, group, job, cf = _setup(db_session)
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

    operator = make_user(db_session, role="operator", tenant=tenant, email="op-refopts-foreign@example.com")
    login(client, operator.email)
    resp = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{foreign_cf.id}/reference-options")
    assert resp.status_code == 404

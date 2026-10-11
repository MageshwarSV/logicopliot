"""correct_field_value's own remember_reference call for an is_target_value field used to pass
match_values=[fv.target_value_raw] - a single-element list, since target_value_raw was always
one key's own raw text. Now that _resolve_target_value can key off several job-level fields at
once (see test_resolve_target_value_multi_key.py), target_value_raw may be "||"-joined, and
this call must split it back into the same ordered keys before remembering a correction -
otherwise a correction on CustomsHouseCode would be learned against the single literal string
"CHENNAI||S" instead of against (CHENNAI, S) together, and would never be found again.
"""
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def test_correcting_a_multi_key_target_value_field_remembers_the_split_keys(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CORRECTMULTI", status="extracted")
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

    # A field that found no match (extraction came back blank) but still carries the raw
    # key pair it tried to look up - exactly what _resolve_target_value leaves behind for a
    # genuinely new port/mode combination the reference table doesn't have yet.
    fv = JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id, label_name=cf.label_name,
        extracted_value="", target_value_raw="VISAKHAPATNAM||S",
    )
    db_session.add(fv)
    db_session.commit()
    db_session.refresh(fv)

    operator = make_user(db_session, role="operator", tenant=tenant, email="op-multicorrect@example.com")
    login(client, operator.email)
    resp = client.patch(f"/api/v1/job-field-values/{fv.id}", json={"corrected_value": "INVTZ1"})
    assert resp.status_code == 200, resp.text

    row = (
        db_session.query(CustomFieldReferenceValue)
        .filter(CustomFieldReferenceValue.custom_field_id == cf.id)
        .one()
    )
    assert row.match_value_1 == "VISAKHAPATNAM"
    assert row.match_value_2 == "S"
    assert row.resolved_value == "INVTZ1"

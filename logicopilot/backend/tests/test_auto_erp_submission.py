"""Auto ERP Submission (app/core/auto_erp.py): straight to the ERP after extraction, no review -
allowed per tenant by the SaaS Admin, switched on per template / branch by the Tenant Admin."""
from unittest.mock import patch

from app.core.auto_erp import fill_operator_defaults, maybe_auto_submit
from app.models.custom_field import CustomField
from app.models.erp_script import ErpScript
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

KICK = "app.api.v1.jobs._kick_off_real_erp_submission"


def _setup(db, *, name="Test Tenant", excel=True, script=True, branch_field=None, allowed=True, enabled=True, branches=None):
    tenant = make_tenant(db, name=name)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import BLR", status="approved", mode="Sea Import",
                          entry_mode="excel" if excel else "fields")
    db.add(group)
    db.commit()
    if script:
        db.add(ErpScript(tenant_id=tenant.id, name="SOFTLINK", url="https://erp.example", status="ready",
                         template_ids=[group.id], branch_field=branch_field))
    tenant.auto_erp_allowed = allowed
    tenant.auto_erp_rules = {group.id: {"enabled": enabled, "branches": branches or []}}
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-AUTO1", status="extracted")
    db.add(job)
    db.commit()
    return tenant, group, job


def test_off_by_default_nothing_happens(db_session):
    tenant, group, job = _setup(db_session, allowed=False)
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()
    assert job.auto_erp_submitted is False and job.auto_erp_note is None


def test_allowed_but_template_not_switched_on(db_session):
    tenant, group, job = _setup(db_session, enabled=False)
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()


def test_switched_on_sends_it_with_no_review(db_session):
    tenant, group, job = _setup(db_session)
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is True
    kick.assert_called_once()
    assert job.auto_erp_submitted is True and job.gk2_skipped is True
    assert job.erp_submitted_by_id is None
    assert "automatically" in job.auto_erp_note
    # Never a second time - e.g. a re-extraction after it went.
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()


def test_cross_doc_rules_not_approved_still_goes(db_session):
    tenant, group, job = _setup(db_session)
    assert job.validation_approved is False
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is True
    kick.assert_called_once()


def test_no_ready_script_or_not_excel_is_held_with_a_reason(db_session):
    _, _, job = _setup(db_session, script=False)
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()
    assert "no ready ERP script" in job.auto_erp_note

    _, _, job2 = _setup(db_session, name="Second Co", excel=False)
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job2) is False
    kick.assert_not_called()
    assert "Excel entry" in job2.auto_erp_note


def test_only_the_listed_branches(db_session):
    _, _, job = _setup(db_session, branch_field="Destination", branches=["Bangalore"])
    with patch(KICK) as kick, patch("app.api.v1.jobs.entry_values_and_rows",
                                    return_value=({"Destination": "Chennai"}, {})):
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()
    assert job.auto_erp_note is None          # another branch: plain manual review

    with patch(KICK) as kick, patch("app.api.v1.jobs.entry_values_and_rows",
                                    return_value=({"Destination": " bangalore "}, {})):
        assert maybe_auto_submit(db_session, job) is True
    kick.assert_called_once()
    assert "Branch: bangalore" in job.auto_erp_note


def test_not_extracted_or_already_in_gk2_is_left_alone(db_session):
    _, _, job = _setup(db_session)
    job.status = "possible_duplicate"
    with patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    job.status = "extracted"
    job.gk2_status = "pending"
    with patch(KICK) as kick2:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()
    kick2.assert_not_called()


def test_empty_operator_pick_gets_the_first_option(db_session):
    tenant, group, job = _setup(db_session)
    pick = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Port Type", kind="hardcoded",
                       ask_operator=True, ask_operator_required=True, fixed_options=["SEA", "ICD"])
    answered = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Mode", kind="hardcoded",
                           ask_operator=True, ask_operator_required=True, fixed_options=["FCL", "LCL"])
    free = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="IGM No", kind="hardcoded",
                       ask_operator=True, ask_operator_required=True)
    db_session.add_all([pick, answered, free])
    db_session.commit()
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=answered.id,
                                 label_name="Mode", corrected_value="LCL"))
    db_session.commit()

    assert fill_operator_defaults(db_session, job) == ["Port Type = SEA"]
    vals = {fv.label_name: fv for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id)}
    assert vals["Port Type"].extracted_value == "SEA" and vals["Port Type"].corrected_value is None
    assert vals["Mode"].value == "LCL"            # a value already there is never replaced
    assert "IGM No" not in vals                   # free text, no options: left blank


# ------------------------------------------------------------------------------------- settings

def test_settings_permissions(client, db_session):
    tenant, group, _ = _setup(db_session, allowed=False, enabled=False)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-autoerp@example.com")
    sa = make_user(db_session, role="super_admin", email="sa-autoerp@example.com")

    login(client, ta.email)
    assert client.put(f"/api/v1/auto-erp/tenants/{tenant.id}/allowed", json={"allowed": True}).status_code == 403
    r = client.put(f"/api/v1/auto-erp/tenants/{tenant.id}/templates/{group.id}", json={"enabled": True})
    assert r.status_code == 409                   # not allowed yet

    login(client, sa.email)
    r = client.put(f"/api/v1/auto-erp/tenants/{tenant.id}/allowed", json={"allowed": True})
    assert r.status_code == 200 and r.json()["allowed"] is True

    login(client, ta.email)
    r = client.put(f"/api/v1/auto-erp/tenants/{tenant.id}/templates/{group.id}",
                   json={"enabled": True, "branches": ["Bangalore", " bangalore ", ""]})
    assert r.status_code == 200, r.text
    row = next(t for t in r.json()["templates"] if t["group_id"] == group.id)
    assert row["enabled"] is True and row["branches"] == ["Bangalore"] and row["problems"] == []

    other = make_tenant(db_session, name="Other Co")
    assert client.get(f"/api/v1/auto-erp/tenants/{other.id}").status_code == 404

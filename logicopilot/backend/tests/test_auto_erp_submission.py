"""Auto ERP Submission (app/core/auto_erp.py): straight to the ERP after extraction, no review -
allowed per tenant by the SaaS Admin, switched on per template / branch by the Tenant Admin."""
from unittest.mock import patch

from app.core.auto_erp import fill_operator_defaults, maybe_auto_submit
from app.core.auto_erp_match import is_junk, match_name, norm_company, pick_branch
from app.models.custom_field_reference import CustomFieldReferenceValue
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

    assert fill_operator_defaults(db_session, job) == ["Port Type = SEA (first choice)"]
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


# ----------------------------------------------------------------- importer matching (auto mode)

def _opt(name, country=""):
    return {"value": name, "match_values": [name.upper(), country, "", ""]}


IMPORTERS = [_opt("."), _opt("JUNIPER NETWORKS INC.", "UNITED STATES"),
             _opt("Juniper Networks International B.V.", "NETHERLANDS"),
             _opt("JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED", "INDIA"),
             _opt("JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED", "")]


def test_junk_and_company_words():
    assert is_junk(".") and is_junk(" - ") and is_junk("N/A") and not is_junk("DELHI")
    assert norm_company("Juniper Networks Solution India Pvt. Ltd.") == \
        norm_company("JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED")


def test_importer_name_exact_after_normalising_no_ai():
    with patch("app.core.auto_erp_match.ai_choose") as ai:
        v, how = match_name("Juniper Networks Solution India Pvt. Ltd.", IMPORTERS, "INDIA")
    ai.assert_not_called()
    assert v == "JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED" and how == "exact match"


def test_importer_name_ambiguous_asks_ai_and_none_close_stays_blank():
    with patch("app.core.auto_erp_match.ai_choose", return_value=0) as ai:
        v, how = match_name("Juniper Networks", IMPORTERS, "INDIA")
    ai.assert_called_once()
    assert how == "AI match" and v == ai.call_args[0][1][0]
    assert "." not in ai.call_args[0][1]                      # junk never offered
    with patch("app.core.auto_erp_match.ai_choose", return_value=None):
        assert match_name("Juniper Networks", IMPORTERS)[0] is None
        assert match_name("Totally Different Traders", IMPORTERS)[0] is None


def test_branch_choice():
    opts = ["BANDRA KURLA", "Bhiwandi", "DELHI", "KARNATAKA", "."]
    assert pick_branch(["ONLY ONE", "."]) == ("ONLY ONE", "only branch")
    assert pick_branch(["CHENNAI", "BANGALORE"], job_branch="Bangalore")[0] == "BANGALORE"
    with patch("app.core.auto_erp_match.ai_choose", return_value=3) as ai:
        v, how = pick_branch(opts, job_branch="Bangalore", address="Whitefield, Bengaluru, Karnataka 560066")
    assert v == "KARNATAKA" and how == "AI from the consignee address"
    assert "." not in ai.call_args[0][1]
    with patch("app.core.auto_erp_match.ai_choose", return_value=None):
        assert pick_branch(opts, address="somewhere") == ("BANDRA KURLA", "first branch")


def test_auto_fill_picks_importer_from_the_document_not_the_first_row(db_session):
    tenant, group, job = _setup(db_session)
    name = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ERP Consignee Name", kind="hardcoded",
                       ask_operator=True, ask_operator_required=True, is_target_value=True)
    branch = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="ERP Importer Address / Branch Code",
                         kind="hardcoded", ask_operator=True, ask_operator_required=True, is_target_value=True)
    db_session.add_all([name, branch])
    db_session.commit()
    for o in IMPORTERS:
        db_session.add(CustomFieldReferenceValue(custom_field_id=name.id, match_value_1=o["match_values"][0],
                                                 match_value_2=o["match_values"][1], resolved_value=o["value"]))
    for b in ("BANDRA KURLA", "KARNATAKA"):
        db_session.add(CustomFieldReferenceValue(custom_field_id=branch.id,
                                                 match_value_1="JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED",
                                                 match_value_2=b, resolved_value=b))
    for label, v in (("Consignee", "Juniper Networks Solution India Pvt. Ltd."),
                     ("Consignee Address", "Prestige Tech Park, Bengaluru, Karnataka"),
                     ("ERP Consignee Country", "INDIA"), ("ERP Consignee Name", ".")):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name=label, extracted_value=v,
                                     custom_field_id=name.id if label == "ERP Consignee Name" else None))
    db_session.commit()

    with patch("app.core.auto_erp_match.ai_choose", return_value=1):
        filled = fill_operator_defaults(db_session, job, "Bangalore")
    vals = {fv.label_name: fv.value for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id)}
    assert vals["ERP Consignee Name"] == "JUNIPER NETWORKS SOLUTION INDIA PRIVATE LIMITED"   # the "." replaced
    assert vals["ERP Importer Address / Branch Code"] == "KARNATAKA"
    assert any("exact match" in f for f in filled) and any("consignee address" in f for f in filled)


def test_one_letter_codes_are_kept_and_code_label_options_give_the_code(db_session):
    tenant, group, job = _setup(db_session)
    be = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="BETypeCode", kind="hardcoded",
                     ask_operator=True, ask_operator_required=True, fixed_options=["H:H - Home", "I:I - In-Bond"])
    prior = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="AdvancePriorNormal", kind="hardcoded",
                        ask_operator=True, ask_operator_required=True, fixed_options=["P:Prior", "N:Normal"])
    db_session.add_all([be, prior])
    db_session.commit()
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=be.id,
                                 label_name="BETypeCode", extracted_value="I"))
    db_session.commit()
    assert fill_operator_defaults(db_session, job) == ["AdvancePriorNormal = P (first choice)"]
    vals = {fv.label_name: fv.value for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id)}
    assert vals == {"BETypeCode": "I", "AdvancePriorNormal": "P"}


def _quotation_setup(db_session, doc_q):
    tenant, group, job = _setup(db_session)
    q = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Erp Entry Name", kind="hardcoded",
                    ask_operator=True, ask_operator_required=True, is_target_value=True)
    db_session.add(q)
    db_session.commit()
    for name in ("AIT WORLDWIDE LOGISTICS INDIA PRIVATE LIMITED", "EXPEDITORS INTERNATIONAL (INDIA) PVT LTD",
                 "KUEHNE+NAGEL PVT LTD"):
        db_session.add(CustomFieldReferenceValue(custom_field_id=q.id, match_value_1=name, resolved_value=name))
    if doc_q:
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="Quotation Name",
                                     extracted_value=doc_q))
    db_session.commit()
    return job


def test_quotation_name_comes_from_the_document_forwarder_not_the_first_row(db_session):
    job = _quotation_setup(db_session, "Expeditors International India Pvt")
    with patch("app.core.auto_erp_match.ai_choose", return_value=0), patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is True
    kick.assert_called_once()
    v = db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id,
                                               JobFieldValue.label_name == "Erp Entry Name").one().value
    assert v == "EXPEDITORS INTERNATIONAL (INDIA) PVT LTD"
    assert "AIT" not in job.auto_erp_note


def test_quotation_name_that_cannot_be_worked_out_holds_the_job(db_session):
    job = _quotation_setup(db_session, None)
    with patch("app.core.auto_erp_match.ai_choose", return_value=None), patch(KICK) as kick:
        assert maybe_auto_submit(db_session, job) is False
    kick.assert_not_called()
    assert job.auto_erp_submitted is False
    assert "held" in job.auto_erp_note and "ERP Quotation Name" in job.auto_erp_note

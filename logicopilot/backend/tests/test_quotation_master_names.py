"""ERP Quotation Name offers and auto-fills only names in the template's Quotation Master
(names with a branch and an address) - JOB-DD47BB showed 'EXPEDITORS INTERNATIONAL OCEAN'."""
from app.api.v1 import jobs as jobs_api
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _setup(db, with_master=True):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved")
    db.add(g)
    db.commit()
    entry = CustomField(tenant_id=t.id, group_id=g.id, label_name="Erp Entry Name", kind="hardcoded",
                        is_target_value=True, lookup_key_label="Quotation Name")
    addr = CustomField(tenant_id=t.id, group_id=g.id, label_name="quotation_address", kind="hardcoded")
    db.add_all([entry, addr])
    db.commit()
    for name in ("EXPEDITORS INTERNATIONAL (INDIA) PVT LTD", "EXPEDITORS INTERNATIONAL OCEAN"):
        db.add(Ref(custom_field_id=entry.id, match_value_1=name, match_value_2="", match_value_3="",
                   match_value_4="", resolved_value=name))
    if with_master:
        db.add(Ref(custom_field_id=addr.id, match_value_1="EXPEDITORS INTERNATIONAL (INDIA) PVT LTD",
                   match_value_2="CHENNAI", match_value_3="", match_value_4="", resolved_value="07th floor"))
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-Q", status="extracted")
    db.add(job)
    db.commit()
    return t, g, entry, job


def test_dropdown_offers_only_master_names(db_session, monkeypatch):
    _t, _g, entry, job = _setup(db_session)
    monkeypatch.setattr(jobs_api, "_load_job", lambda db, jid, scope, user=None: db.get(Job, jid))
    out = jobs_api.list_custom_field_reference_options(job.id, entry.id, None, None, db_session, None, None)
    assert [o["value"] for o in out["options"]] == ["EXPEDITORS INTERNATIONAL (INDIA) PVT LTD"]


def test_without_a_master_nothing_is_narrowed(db_session, monkeypatch):
    _t, _g, entry, job = _setup(db_session, with_master=False)
    monkeypatch.setattr(jobs_api, "_load_job", lambda db, jid, scope, user=None: db.get(Job, jid))
    out = jobs_api.list_custom_field_reference_options(job.id, entry.id, None, None, db_session, None, None)
    assert len(out["options"]) == 2


def test_auto_fill_skips_a_name_outside_the_master(db_session):
    t, _g, entry, job = _setup(db_session)
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Quotation Name",
                                 extracted_value="EXPEDITORS INTERNATIONAL OCEAN"))
    db_session.commit()
    value, raw = jobs_api._resolve_target_value(db_session, job.id, entry, None)
    assert value is None and raw == "EXPEDITORS INTERNATIONAL OCEAN"


def test_auto_fill_keeps_a_master_name(db_session):
    t, _g, entry, job = _setup(db_session)
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Quotation Name",
                                 extracted_value="EXPEDITORS INTERNATIONAL (INDIA) PVT LTD"))
    db_session.commit()
    value, _raw = jobs_api._resolve_target_value(db_session, job.id, entry, None)
    assert value == "EXPEDITORS INTERNATIONAL (INDIA) PVT LTD"

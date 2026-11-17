"""An Indian importer is offered on a job only once its GSTIN is in the dump.

"In the dump, only the ones having a GST number - India only - need to show in the dropdown.
Some will not have one; those need not show. After some time a user adds the GST number in the
dump, and only then does it show."

Switched on per template (TemplateGroup.importer_requires_gstin) - see app/core/importer_gstin.py.
"""
from app.models.custom_field import CustomField
from app.models.custom_field_reference import CustomFieldReferenceValue as Ref
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

GST = "29AAACJ1234A1Z5"


def _setup(db, *, rule=True):
    tenant = make_tenant(db)
    make_user(db, role="super_admin", email="sa-gst@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready",
                          importer_requires_gstin=rule)
    db.add(group)
    db.flush()
    f = {}
    for key, label in (("name", "ERP Consignee Name"), ("branch", "ERP Importer Address / Branch Code"),
                       ("address", "ERP Branch Address"), ("other", "CustomsHouseCode")):
        f[key] = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind="hardcoded")
        db.add(f[key])
    db.flush()

    def importer(name, country, branches):
        db.add(Ref(custom_field_id=f["name"].id, match_value_1=name, match_value_2=country,
                   resolved_value=name.title()))
        for branch, gstin, active in branches:
            db.add(Ref(custom_field_id=f["branch"].id, match_value_1=name, match_value_2=branch,
                       resolved_value=branch.title()))
            db.add(Ref(custom_field_id=f["address"].id, match_value_1=name, match_value_2=branch,
                       match_value_3=country, resolved_value=f"{branch.title()} address",
                       gstin=gstin, is_active=active))

    # India: one branch with a GSTIN, one without
    importer("JUNIPER NETWORKS INDIA", "INDIA", [("BRANCH 1", GST, True), ("BRANCH 5", None, True)])
    # India: no GSTIN anywhere
    importer("JUNIOR TRADERS", "INDIA", [("MAIN BRANCH", None, True), ("PUNE", "  ", True)])
    # India: has a GSTIN, but the branch is switched off
    importer("JUNCTION EXPORTS", "INDIA", [("DELHI", "07AAACJ9999A1Z1", False)])
    # abroad: never has a GSTIN, never hidden
    importer("JUNIPER NETWORKS INC.", "UNITED STATES", [("USA", None, True)])
    db.add(Ref(custom_field_id=f["other"].id, match_value_1="BLR", match_value_2="A", resolved_value="INBLR4"))
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-GST", status="extracted")
    db.add(job)
    db.commit()
    return group, job, f


def _names(client, job, f, country, q="JUN"):
    r = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{f['name'].id}/reference-search",
                   params={"q": q, "country": country})
    assert r.status_code == 200, r.text
    return sorted(x["value"] for x in r.json()["results"])


def _options(client, job, field, **params):
    r = client.get(f"/api/v1/jobs/{job.id}/custom-fields/{field.id}/reference-options", params=params)
    assert r.status_code == 200, r.text
    return sorted(o["value"] for o in r.json()["options"])


def test_only_indian_importers_with_a_gstin_are_offered(client, db_session):
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")

    # the name list: only the importer that has a GSTIN on an active branch
    assert _names(client, job, f, "INDIA") == ["Juniper Networks India"]
    # its branch list: only the branch that has one
    assert _options(client, job, f["branch"], key="JUNIPER NETWORKS INDIA", country="INDIA") == ["Branch 1"]
    # and its address: only that branch's
    assert _options(client, job, f["address"], key="JUNIPER NETWORKS INDIA") == ["Branch 1 address"]


def test_other_countries_are_not_touched(client, db_session):
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")

    assert _names(client, job, f, "UNITED STATES") == ["Juniper Networks Inc."]
    assert _options(client, job, f["branch"], key="JUNIPER NETWORKS INC.", country="UNITED STATES") == ["Usa"]
    assert _options(client, job, f["address"], key="JUNIPER NETWORKS INC.") == ["Usa address"]


def test_adding_the_gstin_in_the_dump_makes_it_show(client, db_session):
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")
    assert "Junior Traders" not in _names(client, job, f, "INDIA")

    row = (db_session.query(Ref).filter(Ref.custom_field_id == f["address"].id,
                                        Ref.match_value_1 == "JUNIOR TRADERS",
                                        Ref.match_value_2 == "MAIN BRANCH").one())
    row.gstin = "27AAACJ5555A1Z9"
    db_session.commit()

    assert _names(client, job, f, "INDIA") == ["Junior Traders", "Juniper Networks India"]
    assert _options(client, job, f["branch"], key="JUNIOR TRADERS", country="INDIA") == ["Main Branch"]
    # the branch whose GSTIN is only spaces still does not count
    assert _options(client, job, f["address"], key="JUNIOR TRADERS") == ["Main Branch address"]


def test_a_gstin_on_a_branch_that_is_switched_off_does_not_count(client, db_session):
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")

    assert "Junction Exports" not in _names(client, job, f, "INDIA")
    assert _options(client, job, f["branch"], key="JUNCTION EXPORTS", country="INDIA") == []


def test_with_the_rule_off_everything_is_offered_as_before(client, db_session):
    group, job, f = _setup(db_session, rule=False)
    login(client, "sa-gst@example.com")

    assert _names(client, job, f, "INDIA") == ["Junction Exports", "Junior Traders", "Juniper Networks India"]
    assert _options(client, job, f["branch"], key="JUNIPER NETWORKS INDIA", country="INDIA") == ["Branch 1", "Branch 5"]
    assert _options(client, job, f["address"], key="JUNIPER NETWORKS INDIA") == ["Branch 1 address", "Branch 5 address"]


def test_a_table_outside_the_importer_cascade_is_never_narrowed(client, db_session):
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")

    assert _options(client, job, f["other"]) == ["INBLR4"]
    assert _options(client, job, f["other"], country="INDIA") == ["INBLR4"]


def test_the_branch_list_is_not_narrowed_until_it_is_told_the_country_is_india(client, db_session):
    """The branch table has no country of its own. A caller that does not say which country it
    is asking about (an older screen, another use of the same list) gets what it always got."""
    group, job, f = _setup(db_session)
    login(client, "sa-gst@example.com")

    assert _options(client, job, f["branch"], key="JUNIPER NETWORKS INDIA") == ["Branch 1", "Branch 5"]


def test_the_switch_is_set_per_template(client, db_session):
    group, job, f = _setup(db_session, rule=False)
    login(client, "sa-gst@example.com")

    r = client.patch(f"/api/v1/template-groups/{group.id}/importer-gstin", json={"importer_requires_gstin": True})
    assert r.status_code == 200 and r.json()["importer_requires_gstin"] is True
    assert _names(client, job, f, "INDIA") == ["Juniper Networks India"]

    r = client.patch(f"/api/v1/template-groups/{group.id}/importer-gstin", json={"importer_requires_gstin": False})
    assert r.json()["importer_requires_gstin"] is False
    assert len(_names(client, job, f, "INDIA")) == 3


# --------------------------------------------------------------------------- #
# A value ALREADY on a job
# --------------------------------------------------------------------------- #
def _job_with_importer(db, group, f, *, country, name, branch, address, status="extracted", ref="JOB-SAVED"):
    from app.models.job import JobFieldValue

    country_cf = (db.query(CustomField)
                  .filter(CustomField.group_id == group.id, CustomField.label_name == "ERP Consignee Country").first())
    if country_cf is None:
        country_cf = CustomField(tenant_id=group.tenant_id, group_id=group.id,
                                 label_name="ERP Consignee Country", kind="hardcoded")
        db.add(country_cf)
        db.flush()
    job = Job(tenant_id=group.tenant_id, group_id=group.id, reference=ref, status=status)
    db.add(job)
    db.flush()
    for cf, value in ((country_cf, country), (f["name"], name), (f["branch"], branch), (f["address"], address)):
        db.add(JobFieldValue(tenant_id=group.tenant_id, job_id=job.id, custom_field_id=cf.id,
                             label_name=cf.label_name, extracted_value=value))
    db.commit()
    return job


def _importer(client, job):
    r = client.get(f"/api/v1/jobs/{job.id}")
    assert r.status_code == 200, r.text
    fields = {fv["label_name"]: fv["value"] or "" for fv in r.json()["field_values"] if fv.get("row_index") is None}
    return (fields.get("ERP Consignee Name"), fields.get("ERP Importer Address / Branch Code"),
            fields.get("ERP Branch Address"))


def test_an_existing_job_whose_importer_has_no_gstin_opens_with_the_importer_empty(client, db_session):
    from app.models.job import JobFieldValue

    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Junior Traders",
                             branch="Main Branch", address="Main Branch address")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("", "", "")
    # what the documents read is still in the record underneath
    db_session.expire_all()
    name_fv = (db_session.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == f["name"].id).one())
    assert name_fv.extracted_value == "Junior Traders" and name_fv.corrected_value == ""


def test_a_branch_without_a_gstin_is_emptied_but_the_importer_stays(client, db_session):
    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Juniper Networks India",
                             branch="Branch 5", address="Branch 5 address")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("Juniper Networks India", "", "")


def test_a_job_whose_importer_and_branch_have_a_gstin_is_left_alone(client, db_session):
    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Juniper Networks India",
                             branch="Branch 1", address="Branch 1 address")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("Juniper Networks India", "Branch 1", "Branch 1 address")


def test_a_foreign_importer_on_a_job_is_left_alone(client, db_session):
    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="United States", name="Juniper Networks Inc.",
                             branch="Usa", address="Usa address")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("Juniper Networks Inc.", "Usa", "Usa address")


def test_a_completed_job_is_never_changed(client, db_session):
    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Junior Traders",
                             branch="Main Branch", address="Main Branch address", status="completed")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("Junior Traders", "Main Branch", "Main Branch address")


def test_with_the_rule_off_a_saved_importer_is_left_alone(client, db_session):
    group, _job, f = _setup(db_session, rule=False)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Junior Traders",
                             branch="Main Branch", address="Main Branch address")
    login(client, "sa-gst@example.com")

    assert _importer(client, job) == ("Junior Traders", "Main Branch", "Main Branch address")


def test_once_the_gstin_is_added_the_importer_can_be_picked_again(client, db_session):
    group, _job, f = _setup(db_session)
    job = _job_with_importer(db_session, group, f, country="INDIA", name="Junior Traders",
                             branch="Main Branch", address="Main Branch address")
    login(client, "sa-gst@example.com")
    assert _importer(client, job) == ("", "", "")

    row = (db_session.query(Ref).filter(Ref.custom_field_id == f["address"].id,
                                        Ref.match_value_1 == "JUNIOR TRADERS",
                                        Ref.match_value_2 == "MAIN BRANCH").one())
    row.gstin = "27AAACJ5555A1Z9"
    db_session.commit()

    # offered again in the lists...
    assert "Junior Traders" in _names(client, job, f, "INDIA")
    assert _options(client, job, f["branch"], key="JUNIOR TRADERS", country="INDIA") == ["Main Branch"]
    # ...and the job stays empty until someone picks it: it is not put back by itself
    assert _importer(client, job) == ("", "", "")

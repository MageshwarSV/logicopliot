"""A field can be marked as holding a DATE, and that reaches the operator's screen.

date_picker is set once by an admin - on a drawn field (FieldMark, e.g. HBL Date, Invoice
Date) or on a custom tag (CustomField) - and puts a calendar button beside that field's box on
the Job Run screen. The box itself is unchanged and still typed into.

These tests follow the PATCH path specifically, because that is how the flag is turned on for
fields that already exist: every live template's date fields were drawn long before this
existed, so enabling them is an edit, never a create.

What the flag must NOT do is change any stored value. A date is kept exactly as its document
wrote it (see extraction._DATE_FORMATS); the ERP robot re-renders it itself and the Excel
export copies the string straight through. The format round-trip that keeps that true lives in
the frontend (utils/fieldDates.ts) and is exercised there; what is checked here is that the
flag travels intact from the wizard to the job screen, and that every field which does not ask
for it is left alone.
"""
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _template(db_session):
    """One group, one document, one drawn date field, one custom tag."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of lading",
                           doc_type="Bill of lading")
    db_session.add(doc)
    db_session.commit()
    mark = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name="HBL Date",
                     x=0.1, y=0.1, width=0.2, height=0.05)
    plain = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name="HBL No",
                      x=0.1, y=0.2, width=0.2, height=0.05)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Invoice Date", kind="ai")
    db_session.add_all([mark, plain, cf])
    db_session.commit()
    return tenant, group, doc, mark, plain, cf


def _super(client, db_session, tenant):
    # No tenant: a super admin is tenant-less by check constraint (ck_users_tenant_scope).
    admin = make_user(db_session, role=SUPER_ADMIN, email="sa-date@example.com")
    login(client, admin.email)
    return admin


# ---- the flag survives the wizard's own save -----------------------------------------------

def test_a_drawn_field_can_be_turned_into_a_date_field(client, db_session):
    tenant, group, doc, mark, plain, cf = _template(db_session)
    _super(client, db_session, tenant)

    r = client.patch(f"/api/v1/marks/{mark.id}",
                     json={"date_picker": True, "date_format": "DD-MMM-YYYY"})
    assert r.status_code == 200, r.text
    assert r.json()["date_picker"] is True
    assert r.json()["date_format"] == "DD-MMM-YYYY"

    db_session.expire_all()
    assert db_session.get(FieldMark, mark.id).date_picker is True


def test_a_custom_tag_can_be_turned_into_a_date_field(client, db_session):
    tenant, group, doc, mark, plain, cf = _template(db_session)
    _super(client, db_session, tenant)

    r = client.patch(f"/api/v1/custom-fields/{cf.id}",
                     json={"date_picker": True, "date_format": "YYYY-MM-DD"})
    assert r.status_code == 200, r.text
    assert r.json()["date_picker"] is True
    assert r.json()["date_format"] == "YYYY-MM-DD"


def test_the_flag_can_be_turned_back_off(client, db_session):
    """Ticked onto the wrong field, an admin must be able to take it off again."""
    tenant, group, doc, mark, plain, cf = _template(db_session)
    _super(client, db_session, tenant)

    client.patch(f"/api/v1/marks/{mark.id}", json={"date_picker": True})
    r = client.patch(f"/api/v1/marks/{mark.id}", json={"date_picker": False})
    assert r.status_code == 200, r.text
    assert r.json()["date_picker"] is False


def test_an_explicit_null_means_off_rather_than_a_500(client, db_session):
    """date_picker is NOT NULL. An older client - or the wizard sending a cleared tick as
    null - must switch it off, not blow up the request."""
    tenant, group, doc, mark, plain, cf = _template(db_session)
    _super(client, db_session, tenant)

    for path in (f"/api/v1/marks/{mark.id}", f"/api/v1/custom-fields/{cf.id}"):
        r = client.patch(path, json={"date_picker": None})
        assert r.status_code == 200, f"{path}: {r.text}"
        assert r.json()["date_picker"] is False


def test_editing_something_else_does_not_disturb_the_flag(client, db_session):
    """The wizard PATCHes only what changed. Renaming a field must not silently un-date it."""
    tenant, group, doc, mark, plain, cf = _template(db_session)
    _super(client, db_session, tenant)

    client.patch(f"/api/v1/marks/{mark.id}",
                 json={"date_picker": True, "date_format": "DD.MM.YYYY"})
    r = client.patch(f"/api/v1/marks/{mark.id}", json={"display_label": "House BL Date"})
    assert r.status_code == 200, r.text
    assert r.json()["date_picker"] is True
    assert r.json()["date_format"] == "DD.MM.YYYY"


# ---- and reaches the operator ---------------------------------------------------------------

def test_the_flag_reaches_the_job_screen_for_both_kinds_of_field(client, db_session):
    """The whole point: an operator opening the job gets the calendar on the date fields and
    on nothing else."""
    tenant, group, doc, mark, plain, cf = _template(db_session)
    mark.date_picker = True
    mark.date_format = "DD-MMM-YYYY"
    cf.date_picker = True
    db_session.commit()

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-date@example.com")
    operator = make_user(db_session, role="operator", tenant=tenant, email="op-date@example.com")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-DATES",
              status="extracted", created_by_id=operator.id, assigned_operator_id=operator.id)
    db_session.add(job)
    db_session.commit()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, set_index=1,
                     template_document_id=doc.id, file_path="/tmp/bl.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, mark_id=mark.id,
                      set_index=1, label_name="HBL Date", extracted_value="15/03/2026"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, mark_id=plain.id,
                      set_index=1, label_name="HBL No", extracted_value="897140033682"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                      set_index=1, label_name="Invoice Date", extracted_value="2026-03-15"),
    ])
    db_session.commit()

    login(client, operator.email)
    r = client.get(f"/api/v1/jobs/{job.id}")
    assert r.status_code == 200, r.text
    by_label = {fv["label_name"]: fv for fv in r.json()["field_values"]}

    assert by_label["HBL Date"]["date_picker"] is True
    assert by_label["HBL Date"]["date_format"] == "DD-MMM-YYYY"
    assert by_label["Invoice Date"]["date_picker"] is True
    # No format set on the custom tag - the screen falls back to the value's own format.
    assert by_label["Invoice Date"]["date_format"] is None

    # Every other field is untouched, which is the guarantee that matters for the 300-odd
    # fields already live on production templates.
    assert by_label["HBL No"]["date_picker"] is False
    assert by_label["HBL No"]["date_format"] is None

    # And the picker changed no stored value on its way through.
    assert by_label["HBL Date"]["extracted_value"] == "15/03/2026"
    assert by_label["Invoice Date"]["extracted_value"] == "2026-03-15"


def test_every_field_defaults_to_not_a_date(client, db_session):
    """Nothing that predates this feature turns into a date field by accident."""
    tenant, group, doc, mark, plain, cf = _template(db_session)
    assert db_session.get(FieldMark, plain.id).date_picker is False
    assert db_session.get(FieldMark, plain.id).date_format is None
    assert db_session.get(CustomField, cf.id).date_picker is False
    assert db_session.get(CustomField, cf.id).date_format is None

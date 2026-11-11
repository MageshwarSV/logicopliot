"""Changing a Combined Description format must not have to rewrite every product line.

Reported on SIT: the format is saved against the CONSIGNEE, so saving it recomputed the
Combined Description on every line of the open job at once. The lines an operator had already
corrected by hand went with it - a format change is not a reason to throw away work on lines
nobody asked about.

So "Save & Apply" now takes the same two shapes the Bulk Update PQU screen already offers:
apply to all product lines, or only to chosen ones. The CONSIGNEE's saved format is unaffected
either way - that is the format for the next job, and is all-or-nothing by its nature; what
narrows is only which lines of the job already open get rewritten.
"""
from app.models.composite_consignee_default import CompositeFieldConsigneeDefault
from app.models.custom_field import CustomField
from app.models.job import JobFieldValue
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user
from tests.test_composite_consignee_default import _add_rows, _make_job, _make_template


def _setup(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-cmb@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-cmb@example.com")
    group, tdoc = _make_template(db_session, tenant)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id,
                               label_name="Combined Description", kind="composite", per_row=True))
    db_session.commit()
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-CMB", consignee="HI-LEX")
    _add_rows(db_session, tenant, job, jd, [
        {"product_description": "WIDGET 1", "Customer Part code": "P1"},
        {"product_description": "WIDGET 2", "Customer Part code": "P2"},
        {"product_description": "WIDGET 3", "Customer Part code": "P3"},
        {"product_description": "WIDGET 4", "Customer Part code": "P4"},
    ])
    login(client, op.email)
    return tenant, group, job


def _save(client, group, job, *, pieces, separators, row_indexes=None):
    body = {"consignee": "HI-LEX", "label_name": "Combined Description",
            "pieces": pieces, "separators": separators, "job_id": job.id}
    if row_indexes is not None:
        body["row_indexes"] = row_indexes
    return client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json=body)


def _combined(db_session, job):
    return {
        fv.row_index: (fv.corrected_value or fv.extracted_value)
        for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.label_name == "Combined Description")
    }


def test_apply_for_all_products_rewrites_every_line(client, db_session):
    """The behaviour that already existed, unchanged when no selection is sent."""
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["Customer Part code", "product_description"],
              separators=["", " "])
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _combined(db_session, job) == {
        1: "P1 WIDGET 1", 2: "P2 WIDGET 2", 3: "P3 WIDGET 3", 4: "P4 WIDGET 4",
    }


def test_apply_for_selected_products_leaves_the_others_alone(client, db_session):
    tenant, group, job = _setup(client, db_session)
    _save(client, group, job, pieces=["Customer Part code", "product_description"],
          separators=["", " "])
    db_session.expire_all()

    # Now a different format, for lines 2 and 3 only.
    r = _save(client, group, job, pieces=["product_description", "Customer Part code"],
              separators=["", " - "], row_indexes=[2, 3])
    assert r.status_code == 200, r.text
    db_session.expire_all()

    assert _combined(db_session, job) == {
        1: "P1 WIDGET 1",            # untouched
        2: "WIDGET 2 - P2",          # rewritten
        3: "WIDGET 3 - P3",          # rewritten
        4: "P4 WIDGET 4",            # untouched
    }


def test_a_hand_corrected_line_outside_the_selection_survives(client, db_session):
    """The actual complaint: an operator's own correction was being overwritten by a format
    change they made about OTHER lines."""
    tenant, group, job = _setup(client, db_session)
    _save(client, group, job, pieces=["Customer Part code", "product_description"],
          separators=["", " "])
    db_session.expire_all()

    fv = (db_session.query(JobFieldValue)
          .filter(JobFieldValue.job_id == job.id,
                  JobFieldValue.label_name == "Combined Description",
                  JobFieldValue.row_index == 4).first())
    fv.corrected_value = "HAND WRITTEN BY A PERSON"
    db_session.commit()

    _save(client, group, job, pieces=["product_description"], separators=[""],
          row_indexes=[1])
    db_session.expire_all()

    combined = _combined(db_session, job)
    assert combined[1] == "WIDGET 1"
    assert combined[4] == "HAND WRITTEN BY A PERSON", "a correction outside the selection was lost"


def test_the_consignee_format_is_saved_in_full_either_way(client, db_session):
    """Selecting lines narrows THIS job only. The consignee's format is what the next job
    computes from, and is all-or-nothing by its nature - it must still be saved whole."""
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["product_description", "Customer Part code"],
              separators=["", " - "], row_indexes=[2])
    assert r.status_code == 200, r.text

    row = db_session.query(CompositeFieldConsigneeDefault).one()
    assert row.source_labels == ["product_description", "Customer Part code"]
    assert row.separators == ["", " - "]


def test_an_empty_selection_means_every_line(client, db_session):
    """[] is "nothing was narrowed", not "write nothing" - otherwise a UI sending an empty
    list would silently apply the format to no line at all and look broken."""
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["Customer Part code"], separators=[""],
              row_indexes=[])
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _combined(db_session, job) == {1: "P1", 2: "P2", 3: "P3", 4: "P4"}


def test_a_line_number_that_does_not_exist_is_simply_ignored(client, db_session):
    tenant, group, job = _setup(client, db_session)
    _save(client, group, job, pieces=["Customer Part code"], separators=[""])
    db_session.expire_all()

    r = _save(client, group, job, pieces=["product_description"], separators=[""],
              row_indexes=[2, 99])
    assert r.status_code == 200, r.text
    db_session.expire_all()
    combined = _combined(db_session, job)
    assert combined[2] == "WIDGET 2"
    assert combined[1] == "P1"
    assert 99 not in combined

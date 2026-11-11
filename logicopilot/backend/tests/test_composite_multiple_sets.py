"""Several Combined Description formats on one job, each over its own stretch of lines.

One format for the whole job is not enough: a shipment can carry products that need genuinely
different descriptions - lines 1-3 built one way, lines 4-6 another. Asked for directly:
"these set will apply to these and these set of row has to apply to these".

Set 1 is the one the CONSIGNEE remembers, because a line RANGE cannot be remembered about a
consignee at all - positions do not survive to the next job, where lines 1-3 are different
products entirely. Sets 2, 3, ... apply to the open job and nowhere else.
"""
from app.models.composite_consignee_default import CompositeFieldConsigneeDefault
from app.models.custom_field import CustomField
from app.models.job import JobFieldValue
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user
from tests.test_composite_consignee_default import _add_rows, _make_job, _make_template


def _setup(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-ms@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-ms@example.com")
    group, tdoc = _make_template(db_session, tenant)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id,
                               label_name="Combined Description", kind="composite", per_row=True))
    db_session.commit()
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-SETS", consignee="HI-LEX")
    _add_rows(db_session, tenant, job, jd, [
        {"product_description": f"WIDGET {n}", "Customer Part code": f"P{n}"} for n in range(1, 7)
    ])
    login(client, op.email)
    return tenant, group, job


def _save(client, group, job, *, pieces, separators, row_indexes=None, extra_sets=None):
    body = {"consignee": "HI-LEX", "label_name": "Combined Description",
            "pieces": pieces, "separators": separators, "job_id": job.id}
    if row_indexes is not None:
        body["row_indexes"] = row_indexes
    if extra_sets is not None:
        body["extra_sets"] = extra_sets
    return client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json=body)


def _combined(db_session, job):
    return {
        fv.row_index: (fv.corrected_value or fv.extracted_value)
        for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.label_name == "Combined Description")
    }


def test_two_sets_each_format_their_own_lines(client, db_session):
    """The whole point: 1-3 one way, 4-6 another, in a single save."""
    tenant, group, job = _setup(client, db_session)
    r = _save(
        client, group, job,
        pieces=["Customer Part code", "product_description"], separators=["", " "],
        row_indexes=[1, 2, 3],
        extra_sets=[{
            "pieces": ["product_description", "Customer Part code"],
            "separators": ["", " - "],
            "row_indexes": [4, 5, 6],
        }],
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _combined(db_session, job) == {
        1: "P1 WIDGET 1", 2: "P2 WIDGET 2", 3: "P3 WIDGET 3",
        4: "WIDGET 4 - P4", 5: "WIDGET 5 - P5", 6: "WIDGET 6 - P6",
    }


def test_three_sets(client, db_session):
    tenant, group, job = _setup(client, db_session)
    r = _save(
        client, group, job,
        pieces=["Customer Part code"], separators=[""], row_indexes=[1, 2],
        extra_sets=[
            {"pieces": ["product_description"], "separators": [""], "row_indexes": [3, 4]},
            {"pieces": [{"fixed": "("}, "Customer Part code", {"fixed": ")"}],
             "separators": ["", "", ""], "row_indexes": [5, 6]},
        ],
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _combined(db_session, job) == {
        1: "P1", 2: "P2",
        3: "WIDGET 3", 4: "WIDGET 4",
        5: "(P5)", 6: "(P6)",
    }


def test_set_1_is_what_the_consignee_remembers(client, db_session):
    """Set 1's format - not set 2's, and not a merge of them - is the one a future job for
    this consignee computes from."""
    tenant, group, job = _setup(client, db_session)
    _save(
        client, group, job,
        pieces=["Customer Part code", "product_description"], separators=["", " "],
        row_indexes=[1, 2, 3],
        extra_sets=[{"pieces": ["product_description"], "separators": [""],
                     "row_indexes": [4, 5, 6]}],
    )
    row = db_session.query(CompositeFieldConsigneeDefault).one()
    assert row.source_labels == ["Customer Part code", "product_description"]
    assert row.separators == ["", " "]


def test_overlapping_sets_are_refused_and_nothing_is_written(client, db_session):
    """Two sets claiming line 3 have no defined answer for it, so the save is refused rather
    than letting the later one quietly win."""
    tenant, group, job = _setup(client, db_session)
    _save(client, group, job, pieces=["Customer Part code"], separators=[""])
    db_session.expire_all()
    before = _combined(db_session, job)

    r = _save(
        client, group, job, pieces=["product_description"], separators=[""],
        row_indexes=[1, 2, 3],
        extra_sets=[{"pieces": ["Customer Part code"], "separators": [""],
                     "row_indexes": [3, 4, 5]}],
    )
    assert r.status_code == 400
    assert "product line 3" in r.json()["detail"]
    db_session.expire_all()
    assert _combined(db_session, job) == before, "a refused save still changed the job"


def test_an_extra_set_with_no_lines_is_refused(client, db_session):
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["Customer Part code"], separators=[""],
              row_indexes=[1], extra_sets=[{"pieces": ["product_description"],
                                            "separators": [""], "row_indexes": []}])
    assert r.status_code == 400
    assert "no product lines" in r.json()["detail"]


def test_an_extra_set_with_no_fields_is_refused(client, db_session):
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["Customer Part code"], separators=[""],
              row_indexes=[1], extra_sets=[{"pieces": [], "separators": [],
                                            "row_indexes": [2]}])
    assert r.status_code == 400
    assert "Set 2 has no fields" in r.json()["detail"]


def test_lines_no_set_covers_keep_what_they_had(client, db_session):
    tenant, group, job = _setup(client, db_session)
    _save(client, group, job, pieces=["Customer Part code"], separators=[""])
    db_session.expire_all()

    _save(client, group, job, pieces=["product_description"], separators=[""],
          row_indexes=[1],
          extra_sets=[{"pieces": [{"fixed": "X"}], "separators": [""], "row_indexes": [2]}])
    db_session.expire_all()
    c = _combined(db_session, job)
    assert c[1] == "WIDGET 1"
    assert c[2] == "X"
    # 3..6 were covered by no set at all.
    assert c[3] == "P3" and c[6] == "P6"


def test_one_set_alone_behaves_exactly_as_before(client, db_session):
    """No extra_sets at all - the existing single-format path, unchanged."""
    tenant, group, job = _setup(client, db_session)
    r = _save(client, group, job, pieces=["Customer Part code", "product_description"],
              separators=["", " "])
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _combined(db_session, job)[1] == "P1 WIDGET 1"
    assert _combined(db_session, job)[6] == "P6 WIDGET 6"

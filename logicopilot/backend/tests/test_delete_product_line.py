"""Removing one product line from a document.

An invoice's line items are read off the page, and the reader sometimes finds rows that are
not products: a subtotal band, a header repeated on page 2, a note inside the goods table.
Each then demands a CTH and a quantity like any real line and blocks submission over
paperwork that does not exist. Re-extracting the whole document and hoping was the only way
out.

Two things this must get right, because both reach customs:

  it takes the WHOLE line        - every value read for it AND its reference-sheet lookups
                                   (CTH, RITC), or the job keeps demanding a code for a line
                                   nobody can see
  it leaves the others alone     - deleting line 2 must not disturb line 3's own values
"""
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import DEFAULT_PASSWORD, login, make_tenant, make_user


def _setup(db_session, rows=3):
    """A job with one document carrying `rows` product lines, each with a read value and a
    looked-up CTH."""
    tenant = make_tenant(db_session)
    operator = make_user(db_session, role="operator", tenant=tenant, email="op@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                            doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ROWS", status="extracted",
              created_by_id=operator.id, assigned_operator_id=operator.id)
    db_session.add(job)
    db_session.commit()
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="/tmp/invoice.pdf", page_count=1, approved=True)
    db_session.add(jd)
    db_session.commit()

    for n in range(1, rows + 1):
        # What the reader took off the page for this line...
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, row_index=n,
            mark_id=None, label_name="product_description",
            extracted_value=f"WIDGET {n}",
        ))
        # ...and the reference-sheet lookup hung off it.
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, job_document_id=jd.id, row_index=n,
            custom_field_id=None, label_name="CTH",
            extracted_value=f"3926909{n}",
        ))
    db_session.commit()
    return tenant, operator, job, jd


def _rows(db_session, job, jd):
    """{row_index: [label -> value]} for what is left on the document."""
    out: dict[int, dict[str, str]] = {}
    for fv in db_session.query(JobFieldValue).filter(
        JobFieldValue.job_id == job.id, JobFieldValue.job_document_id == jd.id,
        JobFieldValue.row_index.isnot(None),
    ).all():
        out.setdefault(fv.row_index, {})[fv.label_name] = fv.extracted_value
    return out


def test_deleting_a_line_takes_every_value_on_it(client, db_session):
    tenant, operator, job, jd = _setup(db_session, rows=3)
    login(client, operator.email)

    response = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")
    assert response.status_code == 200, response.text

    rows = _rows(db_session, job, jd)
    assert len(rows) == 2
    # The read value AND the looked-up CTH are both gone - leaving the CTH behind would keep
    # the job demanding a code for a line nobody can see.
    assert "WIDGET 2" not in [v.get("product_description") for v in rows.values()]
    assert "39269092" not in [v.get("CTH") for v in rows.values()]


def test_the_other_lines_keep_their_own_values(client, db_session):
    """The one that would be invisible until a wrong code reached customs."""
    tenant, operator, job, jd = _setup(db_session, rows=3)
    login(client, operator.email)

    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")

    rows = _rows(db_session, job, jd)
    values = sorted(v["product_description"] for v in rows.values())
    assert values == ["WIDGET 1", "WIDGET 3"]
    # ...each still carrying its OWN cth, not shuffled onto the wrong line by the renumber.
    by_desc = {v["product_description"]: v["CTH"] for v in rows.values()}
    assert by_desc["WIDGET 1"] == "39269091"
    assert by_desc["WIDGET 3"] == "39269093"


def test_remaining_lines_are_renumbered_to_close_the_gap(client, db_session):
    """The screen prints row_index as "Product 3", and so do the validation messages that
    name an unanswered line. A hole would show Product 1, 3, 4 and quote a number that is
    not the one the operator is looking at."""
    tenant, operator, job, jd = _setup(db_session, rows=4)
    login(client, operator.email)

    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")

    rows = _rows(db_session, job, jd)
    assert sorted(rows) == [1, 2, 3]
    # Order is preserved while closing the gap - what was line 3 becomes line 2.
    assert rows[1]["product_description"] == "WIDGET 1"
    assert rows[2]["product_description"] == "WIDGET 3"
    assert rows[3]["product_description"] == "WIDGET 4"


def test_the_last_remaining_line_cannot_be_deleted(client, db_session):
    """A document with no line items at all is not a state any other screen is built for,
    and removing the document itself is the real action there."""
    tenant, operator, job, jd = _setup(db_session, rows=1)
    login(client, operator.email)

    response = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/1")
    assert response.status_code == 409
    assert "only product line" in response.json()["detail"]
    assert len(_rows(db_session, job, jd)) == 1


def test_a_line_that_does_not_exist_is_a_404(client, db_session):
    tenant, operator, job, jd = _setup(db_session, rows=2)
    login(client, operator.email)

    response = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/9")
    assert response.status_code == 404
    assert len(_rows(db_session, job, jd)) == 2


def test_deleting_a_line_un_approves_the_document(client, db_session):
    """The operator has changed what this document says, so an earlier approval no longer
    covers what is on it - the same reasoning deleting a PAGE already uses."""
    tenant, operator, job, jd = _setup(db_session, rows=3)
    login(client, operator.email)
    assert jd.approved is True

    client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")

    db_session.refresh(jd)
    assert jd.approved is False
    assert jd.gk2_approved is False


def test_another_tenant_cannot_delete_a_line(client, db_session):
    """Tenant isolation on a destructive route, checked directly rather than assumed from
    the shared helper."""
    tenant, operator, job, jd = _setup(db_session, rows=3)
    other = make_tenant(db_session, name="Someone Else")
    intruder = make_user(db_session, role="operator", tenant=other, email="them@example.com")
    login(client, intruder.email)

    response = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")
    assert response.status_code in (403, 404)
    assert len(_rows(db_session, job, jd)) == 3


def test_it_is_refused_while_the_job_is_still_being_read(client, db_session):
    """Extraction rewrites these very rows. Deleting one mid-run races it."""
    tenant, operator, job, jd = _setup(db_session, rows=3)
    job.status = "extracting"
    db_session.add(job)
    db_session.commit()
    login(client, operator.email)

    response = client.delete(f"/api/v1/jobs/{job.id}/documents/{jd.id}/rows/2")
    assert response.status_code == 409
    assert len(_rows(db_session, job, jd)) == 3

"""A blank duplicate of a line must not erase the line's real values.

Found on production 2026-11-23 (JOB-D6BE84, Sea import). The same invoice - number
94283641 - sat in the job twice, as a 2-page file and a 4-page file. The first read 51
product lines; the second read 24 blank ones. Both were correctly placed in set 1, because
the pairing in doc_sets.py groups by the invoice number printed on the document and it was
genuinely the same invoice.

Per-row values are keyed on (set_index, row_index) with no document in the key, so lines 1-24
had two rows each - one real, one blank. The dict that feeds a composite field took whichever
row the query returned LAST, and the blank one won: 24 of 51 Combined Descriptions came out
as "-PART NO.(TRANSMISSION PARTS)", the fixed text alone, with the product description and
the part code gone. Re-saving the format could never repair it - the format was never the
problem, and the same helper runs at extraction time too, so the value was wrong from the
first run.

The operator's report was simply "the combined description is not changing when I change the
format", which is what this looks like from the screen.
"""
from app.models.custom_field import CustomField
from app.models.job import JobDocument, JobFieldValue
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user
from tests.test_composite_consignee_default import _add_rows, _make_job, _make_template

PIECES = ["product_description", {"fixed": "-PART NO."}, "Customer Part code",
          {"fixed": "(TRANSMISSION PARTS)"}]
SEPARATORS = ["", " ", "", ""]


def _setup(client, db_session, *, blank_rows):
    """A job whose lines were read from ONE document, plus `blank_rows` empty lines from a
    SECOND document of the same set - exactly the shape the duplicated invoice produced."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-dup@example.com")
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-dup@example.com")
    group, tdoc = _make_template(db_session, tenant)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id,
                               label_name="Combined Description", kind="composite", per_row=True))
    db_session.commit()
    job, jd = _make_job(db_session, tenant, group, tdoc, "JOB-DUP", consignee="ALLISON")
    _add_rows(db_session, tenant, job, jd, [
        {"product_description": "SPRING ASSEMBLY", "Customer Part code": "29531134"},
        {"product_description": "VALVE - MANUAL", "Customer Part code": "29531315"},
        {"product_description": "RETAINER", "Customer Part code": "29545982"},
    ])

    # The second copy of the same invoice. Added AFTER the real rows on purpose: the query
    # that reads them has no ORDER BY, so this is the row that used to win.
    second = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                         file_path="fake/second-copy.pdf", page_count=4, file_index=1)
    db_session.add(second)
    db_session.commit()
    for i in blank_rows:
        for label in ("product_description", "Customer Part code"):
            db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id,
                                         job_document_id=second.id, label_name=label,
                                         extracted_value=None, row_index=i))
    db_session.commit()
    login(client, op.email)
    return group, job


def _save(client, group, job):
    return client.put(f"/api/v1/template-groups/{group.id}/composite-field-config", json={
        "consignee": "ALLISON", "label_name": "Combined Description",
        "pieces": PIECES, "separators": SEPARATORS, "job_id": job.id})


def _combined(db_session, job):
    return {
        fv.row_index: (fv.corrected_value or fv.extracted_value)
        for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id,
            JobFieldValue.label_name == "Combined Description")
    }


def test_a_blank_duplicate_does_not_erase_the_line(client, db_session):
    group, job = _setup(client, db_session, blank_rows=[1, 2])
    assert _save(client, group, job).status_code == 200
    db_session.expire_all()

    # Lines 1 and 2 are the duplicated ones. Before the fix they came back as
    # "-PART NO.(TRANSMISSION PARTS)" - both pieces gone.
    assert _combined(db_session, job) == {
        1: "SPRING ASSEMBLY -PART NO.29531134(TRANSMISSION PARTS)",
        2: "VALVE - MANUAL -PART NO.29531315(TRANSMISSION PARTS)",
        3: "RETAINER -PART NO.29545982(TRANSMISSION PARTS)",
    }


def test_the_unduplicated_lines_were_always_fine(client, db_session):
    """The other half of the production symptom: 27 of 51 lines were correct, which is why
    this read as "only some lines are wrong" rather than as a broken format."""
    group, job = _setup(client, db_session, blank_rows=[])
    assert _save(client, group, job).status_code == 200
    db_session.expire_all()
    assert all("SPRING" in v or "VALVE" in v or "RETAINER" in v
               for v in _combined(db_session, job).values())


def test_a_line_with_genuinely_no_values_still_gets_the_fixed_text(client, db_session):
    """Preferring a non-blank value must not invent one. A line that really has nothing
    keeps the old behaviour - the fixed pieces, and nothing else."""
    group, job = _setup(client, db_session, blank_rows=[])
    second = db_session.query(JobDocument).filter(
        JobDocument.job_id == job.id, JobDocument.file_index == 1).one()
    for label in ("product_description", "Customer Part code"):
        db_session.add(JobFieldValue(tenant_id=job.tenant_id, job_id=job.id,
                                     job_document_id=second.id, label_name=label,
                                     extracted_value=None, row_index=4))
    db_session.commit()
    assert _save(client, group, job).status_code == 200
    db_session.expire_all()
    assert _combined(db_session, job).get(4) in (None, "-PART NO.(TRANSMISSION PARTS)")

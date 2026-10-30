"""Re-reading ONE document instead of the whole job.

When a single file changes - a page cut out, a page added, a reupload - re-reading all four
documents is work nobody asked for: the model is called again for three files whose content
did not move, and the operator is sent back through three approvals they already gave.

The two halves that have to hold together:

  * the other documents keep their values  - they are not deleted and not re-read
  * every CUSTOM field is still recomputed  - a tenant custom field has no job_document_id of
                                              its own, and one with no source_document_ids
                                              reads EVERY document, so there is no honest way
                                              to tell whether it depended on the file that
                                              changed. Leaving them would mean a CTH on a
                                              customs declaration computed from content that
                                              no longer says it.

The delete filter and the per-document skip only make sense together: scoping one without the
other either blanks the untouched documents or leaves duplicates behind.
"""
from unittest.mock import patch

from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _setup(db_session):
    """A job with two documents, each carrying one mark, plus one job-level custom field."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()

    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                               doc_type="Invoice", order_index=0)
    freight = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Freight",
                               doc_type="Custom", order_index=1)
    db_session.add_all([invoice, freight])
    db_session.flush()
    inv_mark = FieldMark(tenant_id=tenant.id, document_id=invoice.id,
                         label_name="Invoice No", page_number=1, x=0, y=0, width=1, height=1)
    frt_mark = FieldMark(tenant_id=tenant.id, document_id=freight.id,
                         label_name="Freight Amount", page_number=1, x=0, y=0, width=1, height=1)
    db_session.add_all([inv_mark, frt_mark])

    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Ins_Currency",
                     kind="hardcoded", hardcoded_value="INR")
    db_session.add(cf)
    db_session.flush()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SCOPED", status="extracted")
    db_session.add(job)
    db_session.flush()
    jd_inv = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=invoice.id,
                         file_path="/tmp/inv.pdf", page_count=1, approved=True)
    jd_frt = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=freight.id,
                         file_path="/tmp/frt.pdf", page_count=1, approved=True)
    db_session.add_all([jd_inv, jd_frt])
    db_session.flush()

    # What a previous whole-job extraction had left behind.
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd_inv.id,
                      mark_id=inv_mark.id, label_name="Invoice No",
                      extracted_value="INV-001"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, job_document_id=jd_frt.id,
                      mark_id=frt_mark.id, label_name="Freight Amount",
                      extracted_value="100.00"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                      label_name="Ins_Currency", extracted_value="STALE"),
    ])
    db_session.commit()
    return tenant, group, job, jd_inv, jd_frt, cf


def _values(db_session, job):
    return {
        (fv.label_name, fv.job_document_id): (fv.extracted_value or "")
        for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    }


def _run_scoped(db_session, job, jd):
    """run_extraction scoped to one document, with the model stubbed out - this is about which
    rows survive and which are rewritten, not about what the model says."""
    from app.api.v1 import jobs as jobs_mod

    with patch.object(jobs_mod, "extract_document_fields", return_value={}), \
         patch.object(jobs_mod, "extract_document_fields_from_images", return_value={}), \
         patch.object(jobs_mod, "_find_duplicate_job", return_value=None):
        jobs_mod.run_extraction(db_session, job, only_job_document_id=jd.id)


def test_the_other_documents_values_survive(db_session):
    """They were not re-read, so they must not be blanked either - the delete filter and the
    per-document skip are one mechanism, and this is the half that proves the filter."""
    tenant, group, job, jd_inv, jd_frt, cf = _setup(db_session)
    _run_scoped(db_session, job, jd_frt)

    db_session.expire_all()
    values = _values(db_session, job)
    assert values.get(("Invoice No", jd_inv.id)) == "INV-001"


def test_the_scoped_documents_own_values_are_cleared_for_rereading(db_session):
    """Its old reading must go, or the re-read lands beside the stale one instead of
    replacing it."""
    tenant, group, job, jd_inv, jd_frt, cf = _setup(db_session)
    _run_scoped(db_session, job, jd_frt)

    db_session.expire_all()
    values = _values(db_session, job)
    # The row is deleted and then written again by the re-read - what must NOT survive is the
    # old reading. (The stubbed model returns nothing, so the fresh row is empty.)
    assert values.get(("Freight Amount", jd_frt.id)) != "100.00"


def test_custom_fields_are_recomputed_even_though_the_run_was_scoped(db_session):
    """The whole safety argument. A custom field cannot be attributed to one document, so it
    is always rebuilt - "STALE" must not survive a scoped run."""
    tenant, group, job, jd_inv, jd_frt, cf = _setup(db_session)
    _run_scoped(db_session, job, jd_frt)

    db_session.expire_all()
    rows = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id)
        .all()
    )
    assert rows, "the custom field must still exist after a scoped run"
    assert all((r.extracted_value or "") != "STALE" for r in rows)


def test_a_scoped_run_leaves_no_duplicate_rows(db_session):
    """Skipping a document whose rows were NOT deleted is the right pairing; getting it wrong
    the other way round (deleting all, skipping some) silently halves a job's data, and this
    is the shape that would show it."""
    tenant, group, job, jd_inv, jd_frt, cf = _setup(db_session)
    _run_scoped(db_session, job, jd_frt)

    db_session.expire_all()
    rows = db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all()
    keys = [(r.label_name, r.job_document_id, r.row_index, r.set_index) for r in rows]
    assert len(keys) == len(set(keys)), keys


def test_an_unscoped_run_still_clears_everything(db_session):
    """The default path is untouched - a full Re-run still wipes and re-reads the lot."""
    from app.api.v1 import jobs as jobs_mod

    tenant, group, job, jd_inv, jd_frt, cf = _setup(db_session)
    with patch.object(jobs_mod, "extract_document_fields", return_value={}), \
         patch.object(jobs_mod, "extract_document_fields_from_images", return_value={}), \
         patch.object(jobs_mod, "_find_duplicate_job", return_value=None):
        jobs_mod.run_extraction(db_session, job)

    db_session.expire_all()
    values = _values(db_session, job)
    # BOTH documents were re-read, so neither keeps its old reading - the difference from a
    # scoped run, where Invoice No stays "INV-001" untouched.
    assert values.get(("Invoice No", jd_inv.id)) != "INV-001"
    assert values.get(("Freight Amount", jd_frt.id)) != "100.00"

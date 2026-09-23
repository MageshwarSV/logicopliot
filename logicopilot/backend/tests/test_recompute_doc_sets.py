"""POST /jobs/{job_id}/recompute-doc-sets — re-running ONLY the invoice/packing-list pairing
(app/core/doc_sets.py) on an already-extracted job, from field values it already has. Built to
recover a job caught by the single-file-per-slot pairing bug without a full /extract re-run,
which would rewrite every field and discard any correction already made."""
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    inv_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    pl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="packing_list")
    db_session.add_all([inv_doc, pl_doc])
    db_session.commit()
    db_session.refresh(inv_doc)
    db_session.refresh(pl_doc)
    inv_mark = FieldMark(tenant_id=tenant.id, document_id=inv_doc.id, label_name="Invoice No",
                         page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    pl_mark = FieldMark(tenant_id=tenant.id, document_id=pl_doc.id, label_name="Invoice No",
                        page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add_all([inv_mark, pl_mark])
    db_session.commit()
    return group, inv_doc, pl_doc, inv_mark, pl_mark


def test_fixes_a_single_invoice_job_split_into_two_fake_sets(client, db_session):
    """The exact live bug: one Invoice file, one Packing List file, whose own 'Invoice No'
    fields read two different strings. Before the doc_sets.py fix this job would already be
    sitting with set_index 1/2 (stored under an OLDER, buggy assign_sets); recompute-doc-sets
    must put both back in the same set without touching either field's own value."""
    tenant = make_tenant(db_session)
    group, inv_doc, pl_doc, inv_mark, pl_mark = _make_template(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SETFIX", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    inv_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv_doc.id,
                         file_path="fake/invoice.pdf", page_count=1, file_index=0, set_index=1)
    pl_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl_doc.id,
                        file_path="fake/packing.pdf", page_count=1, file_index=0, set_index=2)
    db_session.add_all([inv_jd, pl_jd])
    db_session.commit()
    db_session.refresh(inv_jd)
    db_session.refresh(pl_jd)

    inv_fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                           template_document_id=inv_doc.id, job_document_id=inv_jd.id,
                           label_name="Invoice No", extracted_value="ITI0626000011", set_index=1)
    pl_fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_mark.id,
                          template_document_id=pl_doc.id, job_document_id=pl_jd.id,
                          label_name="Invoice No", extracted_value="A54901", set_index=2)
    db_session.add_all([inv_fv, pl_fv])
    db_session.commit()
    db_session.refresh(inv_fv)
    db_session.refresh(pl_fv)

    make_user(db_session, role=SUPER_ADMIN, email="sa-setfix@example.com")
    login(client, "sa-setfix@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/recompute-doc-sets")
    assert resp.status_code == 200, resp.text

    db_session.refresh(inv_jd)
    db_session.refresh(pl_jd)
    db_session.refresh(inv_fv)
    db_session.refresh(pl_fv)
    assert inv_jd.set_index == 1
    assert pl_jd.set_index == 1
    assert inv_fv.set_index == 1
    assert pl_fv.set_index == 1
    # The actual extracted content is untouched - only set_index moved.
    assert inv_fv.extracted_value == "ITI0626000011"
    assert pl_fv.extracted_value == "A54901"


def test_uses_a_corrected_value_over_the_raw_extraction_for_pairing(client, db_session):
    """If an operator corrected the invoice number, the correction is what should drive
    pairing from now on - not whatever the (possibly wrong) original OCR read."""
    tenant = make_tenant(db_session)
    group, inv_doc, pl_doc, inv_mark, pl_mark = _make_template(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CORRECTED", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    inv_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv_doc.id,
                         file_path="fake/invoice.pdf", page_count=1, file_index=0)
    pl_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl_doc.id,
                        file_path="fake/packing.pdf", page_count=1, file_index=0)
    db_session.add_all([inv_jd, pl_jd])
    db_session.commit()
    db_session.refresh(inv_jd)
    db_session.refresh(pl_jd)

    inv_fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                           template_document_id=inv_doc.id, job_document_id=inv_jd.id,
                           label_name="Invoice No", extracted_value="WRONGREAD",
                           corrected_value="E26000505")
    pl_fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_mark.id,
                          template_document_id=pl_doc.id, job_document_id=pl_jd.id,
                          label_name="Invoice No", extracted_value="E26000505")
    db_session.add_all([inv_fv, pl_fv])
    db_session.commit()

    make_user(db_session, role=SUPER_ADMIN, email="sa-corrected@example.com")
    login(client, "sa-corrected@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/recompute-doc-sets")
    assert resp.status_code == 200, resp.text
    db_session.refresh(inv_jd)
    db_session.refresh(pl_jd)
    assert inv_jd.set_index == pl_jd.set_index == 1


def test_tenant_admin_cannot_call_it(client, db_session):
    tenant = make_tenant(db_session)
    group, inv_doc, pl_doc, inv_mark, pl_mark = _make_template(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TA", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-setfix@example.com")
    login(client, "ta-setfix@example.com")

    resp = client.post(f"/api/v1/jobs/{job.id}/recompute-doc-sets")
    assert resp.status_code == 403

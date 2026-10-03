"""_remove_document_file (deleting/reuploading a document's file) used to wipe EVERY custom
field on the job, including kind="hardcoded" fields an operator had manually typed an answer
into (a duty notification number on Additional Details, say) that have no connection to any
document at all. Found live: reuploading one wrong invoice silently destroyed every manual
answer on the whole job, with no way for a later Extract to restore them - Extract only knows
a hardcoded field's static default, never what was actually typed in.

Fixed by excluding kind="hardcoded" from the per-document-removal wipe (see
_remove_document_file's own comment) - a document-dependent field (ai/lookup/composite) still
gets cleared, since ITS value really could be stale; a hardcoded one cannot, by definition,
since it never reads a document in the first place."""
from app.api.v1.jobs import _remove_document_file
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job_with_fields(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)

    manual_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="IGM No",
                            kind="hardcoded", ask_operator=True, ask_operator_required=False)
    ai_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                        kind="ai", ai_prompt="read the consignee")
    db_session.add_all([manual_cf, ai_cf])
    db_session.commit()
    db_session.refresh(manual_cf)
    db_session.refresh(ai_cf)

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MANUAL1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/invoice.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)

    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=manual_cf.id,
                                 label_name="IGM No", corrected_value="IGM-2026-00123"))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=ai_cf.id,
                                 label_name="Consignee", extracted_value="ACME CORP"))
    db_session.commit()
    return job, jd, manual_cf, ai_cf


def test_reupload_keeps_the_operators_own_manual_answer_but_still_clears_document_derived_fields(db_session):
    job, jd, manual_cf, ai_cf = _make_job_with_fields(db_session)

    _remove_document_file(db_session, job, jd)
    db_session.commit()

    manual_value = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == manual_cf.id)
        .first()
    )
    ai_value = (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == ai_cf.id)
        .first()
    )
    assert manual_value is not None
    assert manual_value.corrected_value == "IGM-2026-00123"
    assert ai_value is None
    assert job.needs_reextraction is True

"""accepted_verifications used to be keyed on link identity alone (link.id, or link.id#set)
- so accepting a flagged mismatch, then later correcting the underlying value (via
correct_field_value, recompute_mark or recompute_custom_field - none of which ever touched
accepted_verifications) left the ORIGINAL acceptance silently covering a completely
different, never-reviewed pair of values. The fix folds a short hash of the actual compared
values into the finding's own id, so an old acceptance simply stops matching the moment
either side's value changes - no write path needs to know this mechanism exists."""
from app.api.v1.jobs import _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_two_docs_with_marks(db_session, tenant, group):
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    pkl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="PL")
    db_session.add_all([inv, pkl])
    db_session.commit()
    db_session.refresh(inv)
    db_session.refresh(pkl)
    m1 = FieldMark(tenant_id=tenant.id, document_id=inv.id, label_name="invoice_qty",
                   page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    m2 = FieldMark(tenant_id=tenant.id, document_id=pkl.id, label_name="packing_qty",
                   page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add_all([m1, m2])
    db_session.commit()
    db_session.refresh(m1)
    db_session.refresh(m2)
    return m1, m2


def test_accepting_a_mismatch_then_correcting_the_value_is_no_longer_accepted(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    m1, m2 = _make_two_docs_with_marks(db_session, tenant, group)
    link = CrossDocLink(tenant_id=tenant.id, group_id=group.id, source_mark_id=m1.id, target_mark_id=m2.id)
    db_session.add(link)
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ACCEPT-STALE", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    fv1 = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m1.id,
                        label_name="invoice_qty", extracted_value="1960")
    fv2 = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m2.id,
                        label_name="packing_qty", extracted_value="12788")
    db_session.add_all([fv1, fv2])
    db_session.commit()

    # The operator sees the real mismatch and accepts it.
    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "mismatch"
    job.accepted_verifications = [findings[0]["id"]]
    db_session.commit()

    findings_after_accept = _verification_findings(db_session, job)
    assert findings_after_accept[0]["accepted"] is True

    # A correction changes the value - a DIFFERENT mismatch nobody has looked at yet.
    fv1.corrected_value = "1"
    db_session.commit()

    findings_after_correction = _verification_findings(db_session, job)
    assert findings_after_correction[0]["status"] == "mismatch"
    assert findings_after_correction[0]["accepted"] is False


def test_a_matching_pair_of_values_is_unaffected_by_the_content_key(db_session):
    """Sanity: the new id format still round-trips correctly for an ordinary, unaccepted,
    matching finding - nothing about normal (non-accepted) findings changes."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 2", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    m1, m2 = _make_two_docs_with_marks(db_session, tenant, group)
    link = CrossDocLink(tenant_id=tenant.id, group_id=group.id, source_mark_id=m1.id, target_mark_id=m2.id)
    db_session.add(link)
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ACCEPT-MATCH", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m1.id,
                     label_name="invoice_qty", extracted_value="500"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m2.id,
                     label_name="packing_qty", extracted_value="500"),
    ])
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert findings[0]["status"] == "match"
    assert findings[0]["accepted"] is False


def test_re_accepting_after_a_correction_sticks_to_the_new_values(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import 3", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    m1, m2 = _make_two_docs_with_marks(db_session, tenant, group)
    link = CrossDocLink(tenant_id=tenant.id, group_id=group.id, source_mark_id=m1.id, target_mark_id=m2.id)
    db_session.add(link)
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ACCEPT-REACCEPT", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    fv1 = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m1.id,
                        label_name="invoice_qty", extracted_value="1960")
    fv2 = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=m2.id,
                        label_name="packing_qty", extracted_value="12788")
    db_session.add_all([fv1, fv2])
    db_session.commit()

    findings = _verification_findings(db_session, job)
    job.accepted_verifications = [findings[0]["id"]]
    db_session.commit()

    fv1.corrected_value = "1"
    db_session.commit()
    findings2 = _verification_findings(db_session, job)
    assert findings2[0]["accepted"] is False

    # The operator looks at the NEW mismatch and accepts THAT one.
    job.accepted_verifications = [findings2[0]["id"]]
    db_session.commit()
    findings3 = _verification_findings(db_session, job)
    assert findings3[0]["accepted"] is True

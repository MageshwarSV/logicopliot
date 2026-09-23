"""_verification_findings applies compare_values(party=True) automatically for a link whose
field is a company name/address (Consignee, Exporter, Supplier Name/Address, ...), detected by
label via is_party_field - never for an ordinary text field, where the same leniency would
also forgive a genuinely wrong detail (a changed dosage, a different part code)."""
from app.api.v1.jobs import _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

INVOICE_ADDR = ("ANNORA PHARMA PRIVATE LIMITED SY.NO. 261,ANNARAM VILLAGE,,GUMMADIDALA MANDAL,"
               "SANGAREDDY DISTRICT, HYDERABAD,502313, TELANGANA,India.")
PACKING_LIST_ADDR = ("ANNORA PHARMA PRIVATE LIMITED.,SY.NO.261,PLOT NO.13 TO 14,ANNARAM VILLAGE,"
                     "GUMMADIDAL MANDAL,HYDERABAD SANGAREDDY TELANGANA-502313 INDIA")


def _make_link(db_session, tenant, group, label):
    inv_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                               doc_type="Invoice")
    pl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                              doc_type="Packing List")
    db_session.add_all([inv_doc, pl_doc])
    db_session.commit()
    db_session.refresh(inv_doc)
    db_session.refresh(pl_doc)

    src_mark = FieldMark(tenant_id=tenant.id, document_id=inv_doc.id, label_name=label,
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    tgt_mark = FieldMark(tenant_id=tenant.id, document_id=pl_doc.id, label_name=label,
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add_all([src_mark, tgt_mark])
    db_session.commit()
    db_session.refresh(src_mark)
    db_session.refresh(tgt_mark)

    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=src_mark.id, target_mark_id=tgt_mark.id))
    db_session.commit()
    return src_mark, tgt_mark


def _make_job_with_values(db_session, tenant, group, src_mark, tgt_mark, src_val, tgt_val, label):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PARTY", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=src_mark.id,
                                 label_name=label, extracted_value=src_val, set_index=1))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=tgt_mark.id,
                                 label_name=label, extracted_value=tgt_val, set_index=1))
    db_session.commit()
    return job


def test_a_party_field_forgives_extra_legitimate_address_detail(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    src_mark, tgt_mark = _make_link(db_session, tenant, group, "EXPORTER")
    job = _make_job_with_values(db_session, tenant, group, src_mark, tgt_mark,
                                INVOICE_ADDR, PACKING_LIST_ADDR, "EXPORTER")

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"


def test_an_ordinary_text_field_keeps_the_strict_score_with_the_same_words(db_session):
    """The exact same pair of strings, on a field is_party_field does NOT recognise, must NOT
    get the lenient treatment - proves the leniency is genuinely gated by field type, not a
    blanket change to compare_values' default behaviour."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Export", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    src_mark, tgt_mark = _make_link(db_session, tenant, group, "product_description")
    job = _make_job_with_values(db_session, tenant, group, src_mark, tgt_mark,
                                INVOICE_ADDR, PACKING_LIST_ADDR, "product_description")

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "review"

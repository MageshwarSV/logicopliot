"""_customer_name() — the Jobs list's "Importer/Exporter" column.

A template built for one fixed importer (e.g. NOKIA's own Sea Import template) hardcodes
its consignee as a custom field, so every one of its jobs showed the exact same name in a
column that exists specifically to tell jobs apart. A hardcoded consignee is skipped here -
never shown, never used as a stand-in for a real reading - so once the template also
carries a GENUINE per-job consignee (a mark, or an AI field that reads it off the
document), that is what shows instead. Never the shipper/exporter: showing the other
party's name under an "Importer/Exporter" column that is supposed to be the consignee
would just be a different kind of wrong answer."""
from app.api.v1.jobs import _customer_name
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CUST", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_real_ai_consignee_wins_over_a_hardcoded_one_on_the_same_job(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    hardcoded_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee_full_name",
                               kind="hardcoded", hardcoded_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED")
    real_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Consignee",
                          kind="ai", ai_prompt="Read the consignee off the Bill of Lading.")
    db_session.add_all([hardcoded_cf, real_cf])
    db_session.commit()
    db_session.refresh(hardcoded_cf)
    db_session.refresh(real_cf)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=hardcoded_cf.id,
                     label_name="consignee_full_name",
                     extracted_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=real_cf.id,
                     label_name="Consignee", extracted_value="NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD"),
    ])
    db_session.commit()

    assert _customer_name(db_session, job) == "NOKIA SOLUTIONS AND NETWORKS INDIA PVT LTD"


def test_never_falls_back_to_the_shipper(db_session):
    """The shipper is a different party entirely - showing it under a column meant for the
    consignee would just trade one wrong answer for another, so a hardcoded consignee with
    no real consignee reading anywhere must show nothing, even with a shipper value handy."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    consignee_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee_full_name",
                               kind="hardcoded", hardcoded_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED")
    db_session.add(consignee_cf)
    db_session.commit()
    db_session.refresh(consignee_cf)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=consignee_cf.id,
                                 label_name="consignee_full_name",
                                 extracted_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED"))

    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)
    shipper_mark = FieldMark(tenant_id=tenant.id, document_id=invoice.id, label_name="shipper",
                             page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(shipper_mark)
    db_session.commit()
    db_session.refresh(shipper_mark)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=shipper_mark.id,
                                 template_document_id=invoice.id, label_name="shipper",
                                 extracted_value="4S LOGISTICS SOLUTIONS PVT"))
    db_session.commit()

    assert _customer_name(db_session, job) is None


def test_real_per_job_consignee_still_wins_when_not_hardcoded(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)
    consignee_mark = FieldMark(tenant_id=tenant.id, document_id=invoice.id, label_name="Consignee",
                               page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    shipper_mark = FieldMark(tenant_id=tenant.id, document_id=invoice.id, label_name="shipper",
                             page_number=1, x=0.1, y=0.3, width=0.2, height=0.05)
    db_session.add_all([consignee_mark, shipper_mark])
    db_session.commit()
    for m in (consignee_mark, shipper_mark):
        db_session.refresh(m)
    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=consignee_mark.id,
                     template_document_id=invoice.id, label_name="Consignee",
                     extracted_value="Real Buyer Co"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=shipper_mark.id,
                     template_document_id=invoice.id, label_name="shipper",
                     extracted_value="Real Shipper Co"),
    ])
    db_session.commit()

    assert _customer_name(db_session, job) == "Real Buyer Co"


def test_hardcoded_consignee_alone_falls_back_to_none(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group)

    consignee_cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee_full_name",
                               kind="hardcoded", hardcoded_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED")
    db_session.add(consignee_cf)
    db_session.commit()
    db_session.refresh(consignee_cf)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=consignee_cf.id,
                                 label_name="consignee_full_name",
                                 extracted_value="NOKIA SOLUTIONS AND NETWORKS INDIA PRIVATE LIMITED"))
    db_session.commit()

    assert _customer_name(db_session, job) is None

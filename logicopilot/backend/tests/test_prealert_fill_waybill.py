"""A carrier's waybill arrived with a split pre-alert, so none was generated from the mail - the
HAWB / pieces / gross weight it leaves empty come from the mail's own row for that shipment.

Found live on the MASIMO pre-alert (28 Nov 2026): four FedEx label copies arrived, the HAWB
(printed "TRK# 5472 4211 2765") came out empty on three jobs, though the mail named all four.
"""
from app.api.v1.jobs import _fill_waybill_from_prealert
from app.core.email_puller import _remember_prealert_shipment
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

ROW = {"hawb": "547242112802", "pieces": "3", "gross_weight": "31.1",
       "shipper": "MASIMO MEXICO", "file_no": ""}


def _job(db, readings: dict[str, str | None], *, waybill_file="x/BL_COPY-PLEASE.pdf"):
    tenant = make_tenant(db)
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved",
                          prealert_shipments=True)
    db.add(group)
    db.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill",
                           doc_type="BL", order_index=1)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", order_index=2)
    db.add_all([awb, inv])
    db.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MASIMO", status="extracting")
    db.add(job)
    db.flush()
    awb_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=awb.id,
                         page_count=1, file_path=waybill_file)
    inv_jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                         page_count=2, file_path="x/Invoice.pdf")
    db.add_all([awb_jd, inv_jd])
    db.flush()
    for label, value in readings.items():
        mark = FieldMark(tenant_id=tenant.id, document_id=awb.id, label_name=label,
                         page_number=1, x=0, y=0, width=1, height=1)
        db.add(mark)
        db.flush()
        db.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id,
                             template_document_id=awb.id, job_document_id=awb_jd.id,
                             label_name=label, extracted_value=value))
    # An invoice box with the same kind of label must never be touched.
    inv_mark = FieldMark(tenant_id=tenant.id, document_id=inv.id, label_name="package_count",
                         page_number=1, x=0, y=0, width=1, height=1)
    db.add(inv_mark)
    db.flush()
    db.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                         template_document_id=inv.id, job_document_id=inv_jd.id,
                         label_name="package_count", extracted_value=None))
    db.commit()
    return job


def _values(db, job):
    out = {}
    for fv in db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all():
        doc = db.get(TemplateDocument, fv.template_document_id).name
        out[(doc, fv.label_name)] = fv.extracted_value
    return out


def test_the_row_is_kept_on_the_job_with_only_what_is_needed(db_session):
    job = _job(db_session, {})
    assert _remember_prealert_shipment(db_session, job, dict(ROW)) is True
    db_session.refresh(job)
    assert job.attachment_notes["prealert_shipment"] == {
        "hawb": "547242112802", "pieces": "3", "gross_weight": "31.1"}


def test_other_notes_on_the_job_stay(db_session):
    job = _job(db_session, {})
    job.attachment_notes = {"skipped": ["a.zip"]}
    db_session.commit()
    _remember_prealert_shipment(db_session, job, dict(ROW))
    db_session.refresh(job)
    assert job.attachment_notes["skipped"] == ["a.zip"]
    assert job.attachment_notes["prealert_shipment"]["hawb"] == "547242112802"


def test_nothing_is_kept_without_a_hawb(db_session):
    job = _job(db_session, {})
    assert _remember_prealert_shipment(db_session, job, None) is False
    assert _remember_prealert_shipment(db_session, job, {"pieces": "3"}) is False
    db_session.refresh(job)
    assert not (job.attachment_notes or {}).get("prealert_shipment")


def test_empty_waybill_boxes_are_filled_from_the_mail_s_row(db_session):
    """JOB-ACBCD1: the FedEx copy gave no HAWB, no pieces, no weight."""
    job = _job(db_session, {"HBL No": None, "package_count": None, "Gross Wt": "",
                            "Gross Weight Unit": None, "Supplier Name": "MASIMO MEXICO"})
    _remember_prealert_shipment(db_session, job, dict(ROW))

    filled = _fill_waybill_from_prealert(db_session, job)
    db_session.commit()

    assert sorted(filled) == ["Gross Weight Unit", "Gross Wt", "HBL No", "package_count"]
    v = _values(db_session, job)
    assert v[("Air Waybill", "HBL No")] == "547242112802"
    assert v[("Air Waybill", "package_count")] == "3"
    assert v[("Air Waybill", "Gross Wt")] == "31.1"
    assert v[("Air Waybill", "Gross Weight Unit")] == "KGS"
    assert v[("Air Waybill", "Supplier Name")] == "MASIMO MEXICO"
    assert v[("Invoice", "package_count")] is None


def test_a_value_read_off_the_waybill_always_stays(db_session):
    """JOB-FBC2F0: pieces and weight were read off the label - only the HAWB is filled."""
    job = _job(db_session, {"HBL No": ".", "package_count": "1", "Gross Wt": "124.00",
                            "Gross Weight Unit": "KGS"})
    _remember_prealert_shipment(db_session, job, {"hawb": "547242112765", "pieces": "1",
                                                  "gross_weight": "124"})

    assert _fill_waybill_from_prealert(db_session, job) == ["HBL No"]
    v = _values(db_session, job)
    assert v[("Air Waybill", "HBL No")] == "547242112765"
    assert v[("Air Waybill", "Gross Wt")] == "124.00"


def test_a_weight_with_its_own_unit_leaves_the_unit_box_alone(db_session):
    job = _job(db_session, {"Gross Wt": None, "Gross Weight Unit": None})
    _remember_prealert_shipment(db_session, job, {"hawb": "X1234567", "gross_weight": "31.1 LBS"})

    assert _fill_waybill_from_prealert(db_session, job) == ["Gross Wt"]
    assert _values(db_session, job)[("Air Waybill", "Gross Weight Unit")] is None


def test_a_job_with_no_kept_row_is_untouched(db_session):
    job = _job(db_session, {"HBL No": None})
    assert _fill_waybill_from_prealert(db_session, job) == []
    assert _values(db_session, job)[("Air Waybill", "HBL No")] is None


def test_no_waybill_file_means_nothing_to_fill(db_session):
    job = _job(db_session, {"HBL No": None}, waybill_file=None)
    _remember_prealert_shipment(db_session, job, dict(ROW))
    assert _fill_waybill_from_prealert(db_session, job) == []

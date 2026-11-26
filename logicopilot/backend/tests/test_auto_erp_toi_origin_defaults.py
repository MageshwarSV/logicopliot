"""Auto ERP Submission defaults (4S, 2026-11-26), auto jobs only - app/core/auto_erp_complete.fill_job_values:
  * Terms of Invoice not printed -> FOB, on every invoice of the job;
  * Country of Origin not printed (job Origin and each product line) -> the shipper's country code.
JOB-41A313: three Houston Precision (Ireland) invoices, no TOI on two, no origin anywhere."""
from app.core.auto_erp_complete import fill_job_values
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _job(db, **header):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Air import", status="approved", entry_mode="excel")
    db.add(g)
    db.commit()
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-AUTO", status="extracted", auto_erp_submitted=True)
    db.add(job)
    db.commit()
    for label, value in header.items():
        db.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name=label.replace("_", " ") if label == "Supplier_Country" else label,
                             extracted_value=value))
    db.commit()
    return t, job


def _add(db, t, job, label, value=None, *, row=None, set_index=None, corrected=None):
    fv = JobFieldValue(tenant_id=t.id, job_id=job.id, label_name=label, row_index=row, set_index=set_index,
                       extracted_value=value, corrected_value=corrected)
    db.add(fv)
    db.commit()
    return fv


def test_empty_toi_becomes_fob_on_every_invoice(db_session):
    t, job = _job(db_session)
    a = _add(db_session, t, job, "TOI", "", set_index=1)
    b = _add(db_session, t, job, "TOI", None, set_index=2)
    c = _add(db_session, t, job, "TOI", "CIF", set_index=3)
    d = _add(db_session, t, job, "TOI", "", set_index=4, corrected="EXW")
    notes = fill_job_values(db_session, job)
    assert (a.value, b.value, c.value, d.value) == ("FOB", "FOB", "CIF", "EXW")   # printed / corrected kept
    assert any(n.startswith("TOI = FOB") for n in notes)


def test_empty_origin_takes_the_shippers_country_code(db_session):
    t, job = _job(db_session, Supplier_Country="IE", CountryOfShipmentCode="GB")
    head = _add(db_session, t, job, "Origin", "")
    l1 = _add(db_session, t, job, "CountryOfOriginCode", None, row=1, set_index=1)
    l2 = _add(db_session, t, job, "CountryOfOriginCode", "DE", row=2, set_index=1)
    l3 = _add(db_session, t, job, "CountryOfOriginCode", "", row=1, set_index=2, corrected="CN")
    notes = fill_job_values(db_session, job)
    assert head.value == "IE" and l1.value == "IE"     # the shipper's country, not the AWB's
    assert l2.value == "DE" and l3.value == "CN"       # printed / corrected kept
    assert any("CountryOfOriginCode = IE" in n for n in notes)


def test_without_a_supplier_country_the_country_of_shipment_is_used(db_session):
    t, job = _job(db_session, CountryOfShipmentCode="IE")
    line = _add(db_session, t, job, "CountryOfOriginCode", "", row=1)
    fill_job_values(db_session, job)
    assert line.value == "IE"


def test_no_country_known_leaves_origin_empty(db_session):
    t, job = _job(db_session, **{"Supplier Country": "Ireland"})   # a name, not a code
    line = _add(db_session, t, job, "CountryOfOriginCode", "", row=1)
    fill_job_values(db_session, job)
    assert not (line.value or "")

"""entry_values_and_rows() writes one row per SET onto SET_VALUES_KEY, for an invoice-scoped
sheet to print one row per invoice. But a "set" is just a document slot that doc_sets.assign_sets
could not merge with another - a packing list whose own "Invoice No" reading never matched any
invoice's lands in its own set despite describing the SAME invoice (see assign_sets' own
docstring). Printing one row per SET would then tell the ERP there are two invoices when there
is really one. Only genuinely DIFFERENT invoice numbers should produce separate rows."""
from app.api.v1.jobs import entry_values_and_rows
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-INVDEDUP1", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _add_job_level(db_session, job, label, set_index, value):
    db_session.add(JobFieldValue(
        tenant_id=job.tenant_id, job_id=job.id, label_name=label, set_index=set_index,
        extracted_value=value,
    ))


def test_two_sets_with_the_same_invoice_number_collapse_to_one_row(db_session):
    tenant = make_tenant(db_session)
    job = _make_job(db_session, tenant)
    # set 1: the invoice itself. set 2: its packing list, unpaired by doc_sets (same invoice
    # number, just landed in its own set).
    _add_job_level(db_session, job, "Invoice No", 1, "E26/000505")
    _add_job_level(db_session, job, "Invoice Date", 1, "01-Jan-2026")
    _add_job_level(db_session, job, "Invoice No", 2, "E26/000505")
    _add_job_level(db_session, job, "Invoice Date", 2, "01-Jan-2026")
    db_session.commit()

    from app.core.excel_entry import SET_VALUES_KEY
    values, rows = entry_values_and_rows(db_session, job)
    assert SET_VALUES_KEY not in rows


def test_two_sets_with_genuinely_different_invoice_numbers_stay_separate(db_session):
    tenant = make_tenant(db_session)
    job = _make_job(db_session, tenant)
    _add_job_level(db_session, job, "Invoice No", 1, "72G0083956-1")
    _add_job_level(db_session, job, "Invoice No", 2, "72G0083956-2")
    db_session.commit()

    from app.core.excel_entry import SET_VALUES_KEY
    values, rows = entry_values_and_rows(db_session, job)
    assert len(rows[SET_VALUES_KEY]) == 2


def test_three_sets_two_duplicate_one_different(db_session):
    tenant = make_tenant(db_session)
    job = _make_job(db_session, tenant)
    _add_job_level(db_session, job, "Invoice No", 1, "INV-001")
    _add_job_level(db_session, job, "Invoice No", 2, "INV-001")  # its packing list, unpaired
    _add_job_level(db_session, job, "Invoice No", 3, "INV-002")  # a genuinely second invoice
    db_session.commit()

    from app.core.excel_entry import SET_VALUES_KEY
    values, rows = entry_values_and_rows(db_session, job)
    numbers = [d.get("Invoice No") for d in rows[SET_VALUES_KEY]]
    assert numbers == ["INV-001", "INV-002"]

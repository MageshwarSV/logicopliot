"""Every ITEMS.InvSrNo must name a row that actually exists on the INVOICES sheet.

INVOICES writes one row per DEDUPED invoice, numbered by position. The line items carried the
raw SET they came from. Those are not the same number as soon as dedupe changes the count -
one invoice reaching the job in two sets, which is the ordinary combined invoice + packing
list - and the two numberings drift apart silently.

Production 2026-11-25, MAA/SI/03999/26-27 (JUSDA). Invoice CI26110005 arrived in set 1 and
set 2. The workbook went to the ERP with:

    INVOICES   1 row   InvSrNo = 1
    ITEMS     22 rows  InvSrNo = 2   on every one

The ERP imported the invoice, found no invoice 2 for the items to hang off, dropped all 22
products, and raised NO error. The job filed with its products missing and nothing on any
screen said so - it even reported `ok`, because the run's only success check is that a job
number appeared. The operator re-ran it and got a second empty entry.

Line items now carry the invoice's POSITION after dedupe, so the two sheets always agree.
"""
from app.api.v1.jobs import entry_values_and_rows
from app.core.excel_entry import LINE_SET_KEY, SET_VALUES_KEY
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _job(db_session, tenant, ref):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=ref, status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _job_level(db_session, job, label, set_index, value):
    db_session.add(JobFieldValue(
        tenant_id=job.tenant_id, job_id=job.id, label_name=label,
        set_index=set_index, extracted_value=value,
    ))


def _line(db_session, job, label, set_index, row_index, value):
    db_session.add(JobFieldValue(
        tenant_id=job.tenant_id, job_id=job.id, label_name=label,
        set_index=set_index, row_index=row_index, extracted_value=value,
    ))


def _invoice_count(rows) -> int:
    """How many rows the INVOICES sheet will print - see _rows_for_plan's scope="invoice"."""
    return max(1, len(rows.get(SET_VALUES_KEY) or []))


def test_one_invoice_in_two_sets_numbers_its_items_1(db_session):
    """The production case. Both sets hold invoice CI26110005, so INVOICES prints ONE row -
    and the items must say 1, not the set they came from."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, "JOB-INVSRNO-1")
    for s in (1, 2):
        _job_level(db_session, job, "Invoice No", s, "CI26110005")
        _job_level(db_session, job, "Invoice Date", s, "02-Nov-2026")
    # The products were read off the second set, as they were on the real job.
    for r in (1, 2, 3):
        _line(db_session, job, "product_description", 2, r, f"PART {r}")
    db_session.commit()

    _values, rows = entry_values_and_rows(db_session, job)

    assert _invoice_count(rows) == 1, "one invoice number means one INVOICES row"
    assert rows[LINE_SET_KEY] == ["1", "1", "1"], (
        "every item must point at the single invoice row that exists - "
        f"got {rows[LINE_SET_KEY]}"
    )


def test_two_genuinely_different_invoices_keep_their_own_items(db_session):
    """The case this must NOT break: two real invoices, two INVOICES rows, and each item
    still pointing at its own."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, "JOB-INVSRNO-2")
    _job_level(db_session, job, "Invoice No", 1, "AAA111")
    _job_level(db_session, job, "Invoice No", 2, "BBB222")
    _line(db_session, job, "product_description", 1, 1, "FROM INVOICE ONE")
    _line(db_session, job, "product_description", 2, 2, "FROM INVOICE TWO")
    db_session.commit()

    _values, rows = entry_values_and_rows(db_session, job)

    assert _invoice_count(rows) == 2
    assert rows[LINE_SET_KEY] == ["1", "2"]


def test_every_item_points_at_a_row_that_exists(db_session):
    """The invariant behind both tests above, stated once: no InvSrNo may exceed the number
    of rows INVOICES actually prints. This is what the ERP enforces by silently dropping the
    items - it is the whole bug in one line."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, "JOB-INVSRNO-3")
    for s in (1, 2, 3):
        _job_level(db_session, job, "Invoice No", s, "SAME-NUMBER")
    for r, s in ((1, 1), (2, 3), (3, 2)):
        _line(db_session, job, "product_description", s, r, f"PART {r}")
    db_session.commit()

    _values, rows = entry_values_and_rows(db_session, job)

    printed = _invoice_count(rows)
    for serial in rows[LINE_SET_KEY]:
        assert 1 <= int(serial) <= printed, (
            f"item points at invoice {serial} but INVOICES prints only {printed} row(s)"
        )


def test_a_single_invoice_job_is_untouched(db_session):
    """The ordinary job - one invoice, one set - must come out exactly as it always did."""
    tenant = make_tenant(db_session)
    job = _job(db_session, tenant, "JOB-INVSRNO-4")
    _job_level(db_session, job, "Invoice No", 1, "ONLY-ONE")
    _line(db_session, job, "product_description", 1, 1, "PART 1")
    _line(db_session, job, "product_description", 1, 2, "PART 2")
    db_session.commit()

    _values, rows = entry_values_and_rows(db_session, job)

    assert _invoice_count(rows) == 1
    assert rows[LINE_SET_KEY] == ["1", "1"]

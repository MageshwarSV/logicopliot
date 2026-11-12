"""A packing list that never paired with its invoice is not a second invoice.

doc_sets.assign_sets pairs a packing list to its invoice by the invoice number it prints. When
that reading is blank - or lists several numbers at once ("CD…210/CD…211/…") - the packing list
lands in a set of its own. entry_values_and_rows counted every set as an invoice, so the INVOICES
sheet got an extra row: blank (JOB-F53CB5, JOB-19D603 on SIT) or bogus (MAA/SI/03698/26-27 on
production). The only workaround was typing the invoice number into the packing list by hand.

A set only counts as an invoice when an Invoice-type document is in it, and the Invoice
document's own reading wins over a packing list's copy of the same label.
"""
from app.api.v1.jobs import entry_values_and_rows
from app.core.excel_entry import SET_VALUES_KEY
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _setup(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.flush()
    invoice = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                               doc_type="Invoice", order_index=1)
    packing = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                               doc_type="PackingList", order_index=2)
    db_session.add_all([invoice, packing])
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ORPHANPL", status="extracted")
    db_session.add(job)
    db_session.flush()
    return job, invoice, packing


def _add(db_session, job, doc, label, set_index, value):
    db_session.add(JobFieldValue(
        tenant_id=job.tenant_id, job_id=job.id, template_document_id=doc.id,
        label_name=label, set_index=set_index, extracted_value=value,
    ))


def test_a_packing_list_with_a_blank_invoice_no_adds_no_blank_invoice_row(db_session):
    job, invoice, packing = _setup(db_session)
    _add(db_session, job, invoice, "Invoice No", 1, "463423")
    _add(db_session, job, packing, "Invoice No", 2, "")
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)
    assert SET_VALUES_KEY not in rows          # one invoice -> one row, not two
    assert values["Invoice No"] == "463423"


def test_two_real_invoices_plus_an_orphan_packing_list_gives_two_rows(db_session):
    job, invoice, packing = _setup(db_session)
    _add(db_session, job, invoice, "Invoice No", 1, "0000483453")
    _add(db_session, job, invoice, "Invoice No", 2, "8305")
    _add(db_session, job, packing, "Invoice No", 3, "")
    db_session.commit()

    _values, rows = entry_values_and_rows(db_session, job)
    assert [d.get("Invoice No") for d in rows[SET_VALUES_KEY]] == ["0000483453", "8305"]


def test_a_packing_list_listing_several_numbers_does_not_replace_the_invoice_no(db_session):
    job, invoice, packing = _setup(db_session)
    _add(db_session, job, invoice, "Invoice No", 1, "CD202610000210")
    _add(db_session, job, packing, "Invoice No", 2, "CD202610000210/CD202610000211/CD202610000212")
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)
    assert SET_VALUES_KEY not in rows
    assert values["Invoice No"] == "CD202610000210"


def test_in_the_same_set_the_invoice_documents_reading_wins(db_session):
    job, invoice, packing = _setup(db_session)
    _add(db_session, job, invoice, "Invoice No", 1, "CD202610000408")
    _add(db_session, job, packing, "Invoice No", 1, "D202610000411/CD202610000412")
    db_session.commit()

    values, _rows = entry_values_and_rows(db_session, job)
    assert values["Invoice No"] == "CD202610000408"


def test_a_job_whose_packing_list_paired_normally_is_unchanged(db_session):
    job, invoice, packing = _setup(db_session)
    _add(db_session, job, invoice, "Invoice No", 1, "TW2026294-3")
    _add(db_session, job, packing, "Invoice No", 1, "TW2026294-3")
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)
    assert SET_VALUES_KEY not in rows
    assert values["Invoice No"] == "TW2026294-3"

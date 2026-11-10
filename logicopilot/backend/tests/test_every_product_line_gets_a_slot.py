"""Every product line ends up with a slot on every per-row field - checked, not assumed.

A line with a BLANK CTH is refused by the approval gate, and the operator is told. A line with
NO CTH ROW AT ALL is worse in every way: there is no box on the screen to fill it in, and
nothing for the gate to refuse, so the job can be filed with no tariff heading on that line and
nothing anywhere saying so.

Found on JOB-8BA341 in production: eleven products on the invoice, ten of them with a CTH, and
the job perfectly submittable. The slots are built from a line list read ONCE near the start of
extraction, so a product line written after that read never gets one - and by the time anyone
looked, the job had been re-extracted and the evidence of HOW the eleventh arrived late was
gone with it.

So this does not try to reason about which path adds a line late. It reads the line list again
at the end and compares the two SETS, which holds whatever the cause was and holds at eleven
lines or at a thousand.
"""
from app.api.v1.jobs import _backfill_missing_per_row_slots
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _setup(db_session, product_lines, slots_for):
    """A job with `product_lines` products, where the CTH field only got `slots_for` slots."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()
    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                      kind="lookup", per_row=True, ask_operator=True,
                      ask_operator_required=True)
    job_level = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Importer",
                            kind="ai", per_row=False)
    db_session.add_all([cth, job_level])
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SLOTS", status="extracted")
    db_session.add(job)
    db_session.flush()

    for n in range(1, product_lines + 1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, label_name="product_description",
            row_index=n, set_index=1, extracted_value=f"PRODUCT {n}",
        ))
    for n in range(1, slots_for + 1):
        db_session.add(JobFieldValue(
            tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
            label_name="CTH", row_index=n, set_index=1, extracted_value="85423300",
        ))
    db_session.commit()
    return job, cth, job_level


def _slots(db_session, job, cf):
    return sorted(
        fv.row_index for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id)
    )


def test_a_line_that_arrived_after_the_slots_were_built_still_gets_one(db_session):
    """The production bug, stated directly: eleven products, ten slots."""
    job, cth, job_level = _setup(db_session, product_lines=11, slots_for=10)
    created = _backfill_missing_per_row_slots(db_session, job, [cth, job_level])
    db_session.commit()
    assert created == 1
    assert _slots(db_session, job, cth) == list(range(1, 12))


def test_the_created_slot_is_empty_so_the_gate_refuses_it(db_session):
    """Empty is the honest state for a line nobody has answered. A slot invented with a
    plausible-looking code would be the one outcome worse than no slot at all."""
    job, cth, job_level = _setup(db_session, product_lines=3, slots_for=2)
    _backfill_missing_per_row_slots(db_session, job, [cth, job_level])
    db_session.commit()
    made = db_session.query(JobFieldValue).filter(
        JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cth.id,
        JobFieldValue.row_index == 3).one()
    assert (made.extracted_value or "") == ""
    assert made.corrected_value is None


def test_it_holds_at_a_thousand_lines(db_session):
    """Asked for in those words. It compares the two SETS, so the number does not matter and
    neither does which lines are missing - here every third one is."""
    job, cth, job_level = _setup(db_session, product_lines=1000, slots_for=0)
    for n in range(1, 1001):
        if n % 3 != 0:
            db_session.add(JobFieldValue(
                tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cth.id,
                label_name="CTH", row_index=n, set_index=1, extracted_value="85423300",
            ))
    db_session.commit()

    created = _backfill_missing_per_row_slots(db_session, job, [cth, job_level])
    db_session.commit()
    assert created == 333
    assert _slots(db_session, job, cth) == list(range(1, 1001))


def test_nothing_is_created_when_every_line_already_has_one(db_session):
    job, cth, job_level = _setup(db_session, product_lines=4, slots_for=4)
    assert _backfill_missing_per_row_slots(db_session, job, [cth, job_level]) == 0
    assert _slots(db_session, job, cth) == [1, 2, 3, 4]


def test_a_job_level_field_is_never_given_product_rows(db_session):
    """Only per_row fields have line slots. Handing an Importer eleven rows would invent
    eleven importers for one shipment."""
    job, cth, job_level = _setup(db_session, product_lines=5, slots_for=5)
    _backfill_missing_per_row_slots(db_session, job, [cth, job_level])
    db_session.commit()
    assert _slots(db_session, job, job_level) == []


def test_a_job_with_no_product_lines_is_left_alone(db_session):
    """A job whose documents carry no line items at all must not acquire phantom rows."""
    job, cth, job_level = _setup(db_session, product_lines=0, slots_for=0)
    assert _backfill_missing_per_row_slots(db_session, job, [cth, job_level]) == 0
    assert _slots(db_session, job, cth) == []


def test_each_invoice_set_is_counted_separately(db_session):
    """A line is (invoice-set, line-number), not a line number alone - with two invoices on one
    job there are two "line 1"s, and they are different products."""
    job, cth, job_level = _setup(db_session, product_lines=2, slots_for=2)
    for n in (1, 2):
        db_session.add(JobFieldValue(
            tenant_id=job.tenant_id, job_id=job.id, label_name="product_description",
            row_index=n, set_index=2, extracted_value=f"SECOND INVOICE {n}",
        ))
    db_session.commit()

    created = _backfill_missing_per_row_slots(db_session, job, [cth, job_level])
    db_session.commit()
    assert created == 2
    pairs = sorted(
        (fv.set_index, fv.row_index) for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cth.id)
    )
    assert pairs == [(1, 1), (1, 2), (2, 1), (2, 2)]

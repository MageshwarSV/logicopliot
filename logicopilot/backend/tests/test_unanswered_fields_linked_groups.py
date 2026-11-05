"""_unanswered_operator_fields: "Dump CTH Number" / "Document CTH" / "CTH" (and any other
group of custom fields linked by paired_custom_field_id/sync_field_ids) are three separate
ways of answering the SAME real question for one line, not three independent questions.

Found live: an operator typed the code straight into "CTH" (bypassing the "Dump CTH Number"/
"Document CTH" picker entirely, since neither had a usable value), and Submit still refused,
demanding "Dump CTH Number" - a field nothing had touched, even though the real answer for
this line plainly existed. Confirmed with the user: any ONE member of a linked group having a
value is enough for every member of that group, on that same line."""
from app.api.v1.jobs import _unanswered_operator_fields
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job(db_session, tenant, group):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LINKED-GROUPS", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_linked_trio(db_session, tenant, group):
    """Same shape as the real "Dump CTH Number" / "Document CTH" / "CTH" trio: Dump CTH Number
    is ask_operator+per_row, paired to Document CTH (never itself ask_operator - read straight
    off the document) and synced to CTH (also ask_operator+per_row, but with no link declared
    on ITS OWN side at all - the link only exists because Dump CTH Number named it)."""
    document_cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                               kind="ai", ai_prompt="x", per_row=True, ask_operator=False)
    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH", kind="lookup",
                      per_row=True, ask_operator=True, ask_operator_required=True)
    db_session.add_all([document_cth, cth])
    db_session.commit()
    db_session.refresh(document_cth)
    db_session.refresh(cth)
    dump_cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                           kind="lookup", per_row=True, ask_operator=True, ask_operator_required=True,
                           paired_custom_field_id=document_cth.id, sync_field_ids=[cth.id])
    db_session.add(dump_cth)
    db_session.commit()
    db_session.refresh(dump_cth)
    return dump_cth, document_cth, cth


def test_a_value_typed_directly_into_the_synced_field_answers_the_whole_group(db_session):
    """The exact production bug: nothing on "Dump CTH Number" itself, but "CTH" (its own sync
    target) has a real operator-typed value for this line."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                                 label_name="CTH", row_index=1, corrected_value="85177100"))
    db_session.commit()

    assert _unanswered_operator_fields(db_session, job) == []


def test_a_document_reading_nobody_picked_does_NOT_answer_the_group(db_session):
    """This used to assert the opposite, and the opposite was wrong.

    The premise was that a code read straight off the document is "just as good an answer" as a
    correction on one of the other two. It is not, because nothing exports it. On every live
    template the entry sheet's CTH column reads "Dump CTH Number" and its RITC column reads the
    sync target; "Document CTH" is mapped to no column at all. So a job whose only CTH anywhere
    is an unpicked document reading went out with BOTH of those columns blank - approved,
    submitted, and filed with no tariff heading on the line.

    Reported from the floor as "without CTH it was submitting, it is mandatory right". It is,
    and now it is again. Ticking the box is one click, and making that click is the entire
    purpose of the picker.
    """
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=document_cth.id,
                      label_name="Document CTH", row_index=1, extracted_value="85177100"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=dump_cth.id,
                      label_name="Dump CTH Number", row_index=1, extracted_value=""),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                      label_name="CTH", row_index=1, extracted_value=""),
    ])
    db_session.commit()

    missing = _unanswered_operator_fields(db_session, job)
    assert "Dump CTH Number (line 1)" in missing
    assert "CTH (line 1)" in missing


def test_picking_that_document_reading_then_answers_the_whole_group(db_session):
    """The other half of the test above: one click, and the job is clear to go.

    Picking the document side writes that code into "Dump CTH Number" (the field the export
    reads) and into its sync target. That is what turns a candidate into an answer, and it is
    all the operator has to do.
    """
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=document_cth.id,
                      label_name="Document CTH", row_index=1, extracted_value="85177100"),
        # What the pick writes.
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=dump_cth.id,
                      label_name="Dump CTH Number", row_index=1, corrected_value="85177100"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                      label_name="CTH", row_index=1, corrected_value="85177100"),
    ])
    db_session.commit()

    assert _unanswered_operator_fields(db_session, job) == []


def test_the_dump_sheets_own_reading_still_answers_without_being_retyped(db_session):
    """Unchanged, and must stay unchanged: "Dump CTH Number" is a lookup off the customer's own
    material master, more authoritative than anything an operator would type, and the export
    reads it directly. Requiring it to be retyped to "confirm" it was the busywork that blocked
    a fourteen-line job for no reason."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=dump_cth.id,
                                 label_name="Dump CTH Number", row_index=1,
                                 extracted_value="85177100"))
    db_session.commit()

    assert _unanswered_operator_fields(db_session, job) == []


def test_all_three_blank_still_asks_for_both_ask_operator_members(db_session):
    """No silent suppression - when nothing in the group has anything, both ask_operator
    members of it are still named, same as before this change."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=dump_cth.id,
                     label_name="Dump CTH Number", row_index=1, extracted_value=""),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                     label_name="CTH", row_index=1, extracted_value=""),
    ])
    db_session.commit()

    missing = _unanswered_operator_fields(db_session, job)
    assert "Dump CTH Number (line 1)" in missing
    assert "CTH (line 1)" in missing


def test_the_group_link_is_scoped_to_its_own_line_not_every_line(db_session):
    """A value on line 1 must never satisfy line 2's own, separate requirement."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add_all([
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                     label_name="CTH", row_index=1, corrected_value="85177100"),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=dump_cth.id,
                     label_name="Dump CTH Number", row_index=2, extracted_value=""),
        JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cth.id,
                     label_name="CTH", row_index=2, extracted_value=""),
    ])
    db_session.commit()

    missing = _unanswered_operator_fields(db_session, job)
    assert "Dump CTH Number (line 2)" in missing
    assert "CTH (line 2)" in missing
    assert not any("line 1" in m for m in missing)


def test_an_unlinked_per_row_ask_field_still_needs_its_own_correction(db_session):
    """Regression: a field with no pairing/sync at all is completely unaffected - an extracted
    value alone (no correction) still does not count as answered for it."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    plain = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Some Other Field",
                        kind="ai", per_row=True, ask_operator=True, ask_operator_required=True)
    db_session.add(plain)
    db_session.commit()
    db_session.refresh(plain)
    job = _make_job(db_session, tenant, group)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=plain.id,
                                 label_name="Some Other Field", row_index=1, extracted_value="guessed"))
    db_session.commit()

    assert _unanswered_operator_fields(db_session, job) == ["Some Other Field (line 1)"]

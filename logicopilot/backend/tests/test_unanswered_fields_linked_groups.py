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


def test_a_value_read_onto_document_cth_answers_the_group_even_though_it_is_never_asked(db_session):
    """"Document CTH" is never itself ask_operator - nobody is ever asked to confirm it - but a
    real value read straight off the document is just as good an answer for the group as an
    explicit correction on one of the OTHER two."""
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    dump_cth, document_cth, cth = _make_linked_trio(db_session, tenant, group)
    job = _make_job(db_session, tenant, group)

    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=document_cth.id,
                                 label_name="Document CTH", row_index=1, extracted_value="85177100"))
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

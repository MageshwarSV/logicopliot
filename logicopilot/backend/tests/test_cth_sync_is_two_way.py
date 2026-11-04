"""A code typed into the big CTH box has to reach "Dump CTH Number" as well.

The entry sheet has TWO code columns, and they do not read the box you would expect:

    column I  "CTH"   <- the "Dump CTH Number" field
    column J  "RITC"  <- the "CTH" field

So the box labelled CTH is not what fills the column headed CTH. The link between them
(sync_field_ids) was written down on one side only - "Dump CTH Number" names "CTH", and CTH
names nothing back - and the code honoured it one way to match.

That meant two ways of entering the same code behaved differently:

  * tick either checkbox -> fills Dump AND CTH -> both columns right
  * type into the big CTH box -> fills CTH only -> column I goes out BLANK

Nothing warned anyone, because the box the operator had just typed into was full. The screen
looked complete and the declaration had no tariff heading on it.

What the link actually states is "these two boxes are the same answer", which is not a one-way
statement. These tests pin that it is now read both ways - and that "Document CTH", which is a
separate READING rather than the same answer, is still never overwritten by either of them.
"""
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session, lines=2):
    """The real trio: Dump CTH Number -> paired to Document CTH, synced to CTH."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-twoway@example.com")
    login(client, "sa-twoway@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()

    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                      kind="lookup", per_row=True, ask_operator=True,
                      ask_operator_required=True)
    document = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                           kind="ai", per_row=True, ask_operator=False)
    db_session.add_all([cth, document])
    db_session.flush()
    dump = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                       kind="lookup", per_row=True, ask_operator=True,
                       ask_operator_required=True,
                       paired_custom_field_id=document.id, sync_field_ids=[cth.id])
    db_session.add(dump)
    db_session.flush()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TWOWAY", status="draft")
    db_session.add(job)
    db_session.flush()

    fvs = {}
    for n in range(1, lines + 1):
        for cf, extracted in ((dump, ""), (document, f"2000000{n}"), (cth, "")):
            fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                               label_name=cf.label_name, row_index=n, extracted_value=extracted)
            db_session.add(fv)
            db_session.flush()
            fvs[(cf.label_name, n)] = fv
    db_session.commit()
    return job, fvs


def _value(db_session, fv):
    db_session.refresh(fv)
    return (fv.corrected_value if fv.corrected_value is not None else fv.extracted_value) or ""


def test_typing_into_the_cth_box_also_fills_dump_cth_number(client, db_session):
    """The bug, stated plainly: column I used to come out empty after this."""
    job, fvs = _setup(client, db_session)
    r = client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                     json={"corrected_value": "85423300"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs[("Dump CTH Number", 1)]) == "85423300"
    assert _value(db_session, fvs[("CTH", 1)]) == "85423300"


def test_it_still_works_the_original_way_round(client, db_session):
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('Dump CTH Number', 1)].id}",
                 json={"corrected_value": "85412900"})
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "85412900"


def test_the_document_reading_is_never_overwritten_by_either(client, db_session):
    """Document CTH is what the picker exists to compare AGAINST. Carrying a correction into
    it would destroy the only record of what the supplier actually printed."""
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "85423300"})
    db_session.expire_all()
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"


def test_it_only_touches_its_OWN_line(client, db_session):
    """A single save is a single line. Carrying across lines is a separate, explicit action
    (carry-across-rows), and it must stay that way or "only this line" means nothing."""
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "85423300"})
    db_session.expire_all()
    assert _value(db_session, fvs[("Dump CTH Number", 2)]) == ""
    assert _value(db_session, fvs[("CTH", 2)]) == ""


def test_clearing_the_cth_box_clears_dump_too(client, db_session):
    """Unticking a box clears the answer. If that only cleared one of the two, the sheet would
    still carry the old code in column I with nothing on screen to show for it."""
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "85423300"})
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": ""})
    db_session.expire_all()
    assert _value(db_session, fvs[("Dump CTH Number", 1)]) == ""
    assert _value(db_session, fvs[("CTH", 1)]) == ""


def test_the_big_cth_box_cannot_be_left_empty_even_when_the_dump_found_a_code(client, db_session):
    """"The bigger CTH field should not submit empty."

    The dump sheet's own reading answers for ITSELF without being retyped - that part is
    deliberate and unchanged. What it may no longer do is answer for the big CTH box, which is
    its own column on the entry sheet and was being approved visibly blank.
    """
    from app.api.v1.jobs import _unanswered_operator_fields

    job, fvs = _setup(client, db_session)
    fvs[("Dump CTH Number", 1)].extracted_value = "85412900"
    db_session.commit()

    missing = _unanswered_operator_fields(db_session, job)
    assert "CTH (line 1)" in missing
    assert "Dump CTH Number (line 1)" not in missing


def test_clearing_the_dump_box_does_NOT_clear_the_document_reading(client, db_session):
    """Asked directly, because the wording of a commit message made it sound otherwise.

    Clearing travels between "Dump CTH Number" and "CTH" - the two boxes that hold the ANSWER.
    It never reaches "Document CTH", which holds what the supplier printed. That reading is the
    operator's evidence; nothing an operator does to the answer should erase the evidence.
    """
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('Dump CTH Number', 1)].id}",
                 json={"corrected_value": "85412900"})
    client.patch(f"/api/v1/job-field-values/{fvs[('Dump CTH Number', 1)].id}",
                 json={"corrected_value": ""})
    db_session.expire_all()
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"   # untouched
    assert _value(db_session, fvs[("CTH", 1)]) == ""                     # cleared with it

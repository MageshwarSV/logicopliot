"""The CTH sync is a diode: the candidates feed the answer, and nothing feeds back.

    Dump CTH Number  ---->  CTH        a tick, or an edit, flows down
    Document CTH     ---->  CTH
    CTH              -/->   either     what is typed in the big box stays there

The two small boxes are EVIDENCE. One is the code on the customer's own master sheet, the other
is the code the supplier printed on the invoice. The big box is the ANSWER. Evidence that
rewrites itself to match the answer has stopped being evidence - and an operator comparing two
boxes that always agree is choosing between nothing.

This file replaces test_cth_sync_is_two_way.py, which pinned the opposite. That attempt was
driven by the entry sheet's CTH column reading "Dump CTH Number" rather than "CTH", so the
answer had to be forced back into the candidate for the export to see it. That is a mapping
problem and belongs in the template, not in the data model.
"""
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session, lines=2):
    """The real trio: Dump CTH Number -> paired to Document CTH, synced to CTH."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-oneway@example.com")
    login(client, "sa-oneway@example.com")
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

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ONEWAY", status="draft")
    db_session.add(job)
    db_session.flush()

    fvs = {}
    for n in range(1, lines + 1):
        for cf, extracted in ((dump, f"1000000{n}"), (document, f"2000000{n}"), (cth, "")):
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


# ----------------------------------------------------------------- the diode, forwards

def test_an_edit_on_the_dump_box_flows_down_into_cth(client, db_session):
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('Dump CTH Number', 1)].id}",
                 json={"corrected_value": "85412900"})
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "85412900"


def test_ticking_the_document_side_writes_only_the_answer_box(client, db_session):
    """The one that was asked for in exactly these words: the document's code must NOT land in
    the dump box. The dump box says what the customer's master sheet says, full stop."""
    job, fvs = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "other",
    })
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "20000001"              # answer written
    assert _value(db_session, fvs[("Dump CTH Number", 1)]) == "10000001"  # candidate untouched
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"     # candidate untouched


def test_ticking_the_dump_side_also_writes_only_the_answer_box(client, db_session):
    job, fvs = _setup(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "primary",
    })
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "10000001"
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"


def test_every_line_takes_its_OWN_document_code_not_line_ones(client, db_session):
    """A tick carries WHICH SIDE is right, never line 1's actual code."""
    job, fvs = _setup(client, db_session, lines=2)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "other",
    })
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "20000001"
    assert _value(db_session, fvs[("CTH", 2)]) == "20000002"


# ----------------------------------------------------------------- the diode, backwards

def test_typing_into_the_big_cth_box_does_NOT_reach_the_dump_box(client, db_session):
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "99999999"})
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "99999999"
    assert _value(db_session, fvs[("Dump CTH Number", 1)]) == "10000001"


def test_typing_into_the_big_cth_box_does_NOT_reach_the_document_box(client, db_session):
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "99999999"})
    db_session.expire_all()
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"


def test_clearing_the_big_cth_box_leaves_both_candidates_standing(client, db_session):
    """Unticking clears the ANSWER so the operator can choose again or type their own. What the
    sheet and the document each said does not stop being true because nothing is chosen yet."""
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}",
                 json={"corrected_value": "99999999"})
    client.patch(f"/api/v1/job-field-values/{fvs[('CTH', 1)].id}", json={"corrected_value": ""})
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == ""
    assert _value(db_session, fvs[("Dump CTH Number", 1)]) == "10000001"
    assert _value(db_session, fvs[("Document CTH", 1)]) == "20000001"


def test_an_edit_never_reaches_another_line(client, db_session):
    """A single save is a single line. Carrying across lines is its own explicit action, or
    "only this line" would mean nothing."""
    job, fvs = _setup(client, db_session)
    client.patch(f"/api/v1/job-field-values/{fvs[('Dump CTH Number', 1)].id}",
                 json={"corrected_value": "85412900"})
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 2)]) == ""
    assert _value(db_session, fvs[("Dump CTH Number", 2)]) == "10000002"


# ----------------------------------------------------------------- and the big box is required

def test_the_big_cth_box_cannot_be_left_empty_even_when_the_dump_found_a_code(client, db_session):
    """"The bigger CTH field should not submit empty."

    The dump sheet's own reading still answers for ITSELF without being retyped - that is
    deliberate and unchanged. What it may no longer do is answer for the big CTH box, which is
    its own column on the entry sheet and was being approved visibly blank.
    """
    from app.api.v1.jobs import _unanswered_operator_fields

    job, fvs = _setup(client, db_session)
    missing = _unanswered_operator_fields(db_session, job)
    assert "CTH (line 1)" in missing
    assert "Dump CTH Number (line 1)" not in missing


def test_ticking_a_box_is_enough_to_clear_that_requirement(client, db_session):
    from app.api.v1.jobs import _unanswered_operator_fields

    job, fvs = _setup(client, db_session, lines=1)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "other",
    })
    db_session.expire_all()
    assert _unanswered_operator_fields(db_session, job) == []


def test_ticking_the_dump_works_when_the_document_side_is_blank(client, db_session):
    """The invoice simply does not print a code - which is most Foxconn invoices.

    The guard here used to test the OTHER side: "nothing to pick between on this line, leave
    it alone". So on a job whose Document CTH is blank on every line, ticking "Dump CTH Number"
    skipped every row and wrote nothing. The tick appeared and the CTH stayed empty - reported
    as "I have given the tick mark but the value is not taken in the box".

    What matters is whether the side they PICKED has something, not the other one.
    """
    job, fvs = _setup(client, db_session, lines=2)
    for n in (1, 2):
        fvs[("Document CTH", n)].extracted_value = ""
    db_session.commit()

    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "primary",
    })
    assert r.status_code == 200, r.text
    assert r.json()["rows_written"] == 2
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "10000001"
    assert _value(db_session, fvs[("CTH", 2)]) == "10000002"


def test_a_line_with_nothing_on_the_chosen_side_is_still_left_alone(client, db_session):
    """The protection the old guard was reaching for, stated correctly. Line 2's document is
    blank, so picking Document must leave line 2 as it was rather than emptying it on the
    strength of a choice made on line 1."""
    job, fvs = _setup(client, db_session, lines=2)
    fvs[("CTH", 2)].extracted_value = "77777777"
    fvs[("Document CTH", 2)].extracted_value = ""
    db_session.commit()

    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": fvs[("Dump CTH Number", 1)].custom_field_id,
        "mode": "pick", "which": "other",
    })
    assert r.json()["rows_written"] == 1
    db_session.expire_all()
    assert _value(db_session, fvs[("CTH", 1)]) == "20000001"
    assert _value(db_session, fvs[("CTH", 2)]) == "77777777"

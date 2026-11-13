"""Carrying a CTH across every product line, in one request.

An invoice's lines are nearly always the same commodity, so a code chosen or typed on one
line is right for the rest. The page used to do that itself, a line at a time: one request
per line plus one per field it syncs to, close to three hundred on a 144-line invoice, sent
strictly one after another. The card showed "saving…" for about a minute and operators
reasonably concluded it had hung.

What these tests pin is not the speed - it is that doing it in one request still does the
SAME thing. Two rules in particular are the difference between a time-saver and a wrong
customs declaration:

  * a "pick" gives every line its OWN value from that side, never line 1's value copied
    everywhere - line 7's document reading is line 7's answer
  * a pick writes the ANSWER field only. The two candidate boxes - the customer's master sheet
    on one side, what the supplier printed on the other - are evidence, and are never rewritten
    to agree with the choice made between them
  * a carried VALUE skips any line that already carries a code of its own, so correcting
    line 1 on a mixed-commodity invoice cannot flatten line 7
"""
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _setup(client, db_session, lines=3):
    """A job with `lines` product rows, each carrying a Dump/Document pairing feeding a CTH."""
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-carry@example.com")
    login(client, "sa-carry@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", status="ready")
    db_session.add(group)
    db_session.flush()

    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                      kind="lookup", per_row=True)
    document = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                           kind="ai", per_row=True)
    db_session.add_all([cth, document])
    db_session.flush()
    dump = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                       kind="lookup", per_row=True,
                       paired_custom_field_id=document.id, sync_field_ids=[cth.id])
    db_session.add(dump)
    db_session.flush()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TEST", status="draft")
    db_session.add(job)
    db_session.flush()

    for n in range(1, lines + 1):
        for cf, extracted in (
            (dump, f"1000000{n}"),
            (document, f"2000000{n}"),
            (cth, f"1000000{n}"),
        ):
            db_session.add(JobFieldValue(
                tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                label_name=cf.label_name, row_index=n, extracted_value=extracted,
            ))
    db_session.commit()
    return job, dump, document, cth


def _values(db_session, job, cf):
    return [
        (fv.row_index, (fv.corrected_value if fv.corrected_value is not None
                        else fv.extracted_value))
        for fv in sorted(
            db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id,
                    JobFieldValue.custom_field_id == cf.id).all(),
            key=lambda f: f.row_index,
        )
    ]


def test_a_pick_gives_every_line_its_OWN_value_from_that_side(client, db_session):
    # The thing that must never happen: line 1's code copied onto lines 2 and 3. Picking
    # "Document" means each line takes ITS OWN document reading.
    job, dump, document, cth = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other",
    })
    assert r.status_code == 200, r.text
    assert r.json()["rows_written"] == 3
    db_session.expire_all()
    assert _values(db_session, job, cth) == [(1, "20000001"), (2, "20000002"), (3, "20000003")]
    # ... and the dump box still says what the customer's master sheet says.
    assert _values(db_session, job, dump) == [(1, "10000001"), (2, "10000002"), (3, "10000003")]


def test_a_pick_carries_to_the_synced_field_too(client, db_session):
    # The CTH is what the export actually reads, and it is hidden behind the picker, so a
    # pick that did not reach it would look right on screen and file the wrong code.
    job, dump, document, cth = _setup(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other",
    })
    db_session.expire_all()
    assert _values(db_session, job, cth) == [(1, "20000001"), (2, "20000002"), (3, "20000003")]


def test_picking_the_dump_side_puts_every_line_back_on_its_own_dump_value(client, db_session):
    job, dump, document, cth = _setup(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other"})
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "primary"})
    db_session.expire_all()
    assert _values(db_session, job, cth) == [(1, "10000001"), (2, "10000002"), (3, "10000003")]


def test_a_line_with_nothing_on_the_other_side_is_left_alone(client, db_session):
    # Blanking a line out on the strength of a choice made on a DIFFERENT line would lose a
    # code the operator already had.
    job, dump, document, cth = _setup(client, db_session)
    doc_row2 = (db_session.query(JobFieldValue)
                .filter(JobFieldValue.job_id == job.id,
                        JobFieldValue.custom_field_id == document.id,
                        JobFieldValue.row_index == 2).one())
    doc_row2.extracted_value = ""
    db_session.commit()

    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other"})
    assert r.json()["rows_written"] == 2
    db_session.expire_all()
    # Line 2 had nothing on the document side, so its answer is left exactly as it was.
    assert _values(db_session, job, cth) == [(1, "20000001"), (2, "10000002"), (3, "20000003")]


def test_a_typed_value_fills_every_line_that_was_following_along(client, db_session):
    job, dump, document, cth = _setup(client, db_session)
    # Every line currently shows its own dump value; line 1 has just been retyped to 99999999,
    # so `previous` is what line 1 USED to hold.
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value",
        "value": "99999999", "previous": "10000001",
    })
    assert r.status_code == 200, r.text
    db_session.expire_all()
    # Only the line that actually held `previous` follows - the others carry codes of their
    # own and keep them.
    assert _values(db_session, job, cth) == [(1, "99999999"), (2, "10000002"), (3, "10000003")]


def test_a_typed_value_fills_the_lines_that_are_empty(client, db_session):
    job, dump, document, cth = _setup(client, db_session)
    for fv in (db_session.query(JobFieldValue)
               .filter(JobFieldValue.job_id == job.id,
                       JobFieldValue.custom_field_id == cth.id).all()):
        fv.extracted_value = ""
    db_session.commit()

    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value", "value": "88888888", "previous": "",
    })
    db_session.expire_all()
    assert _values(db_session, job, cth) == [(1, "88888888"), (2, "88888888"), (3, "88888888")]


def test_a_line_someone_deliberately_set_differently_is_never_flattened(client, db_session):
    # The mixed-commodity invoice. Line 2 genuinely differs; correcting line 1 afterwards
    # must not take line 2 with it.
    job, dump, document, cth = _setup(client, db_session)
    row2 = (db_session.query(JobFieldValue)
            .filter(JobFieldValue.job_id == job.id,
                    JobFieldValue.custom_field_id == cth.id,
                    JobFieldValue.row_index == 2).one())
    row2.corrected_value = "77777777"
    db_session.commit()

    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value",
        "value": "99999999", "previous": "10000001",
    })
    db_session.expire_all()
    assert dict(_values(db_session, job, cth))[2] == "77777777"


def test_a_value_is_stripped_the_same_way_a_single_save_strips_it(client, db_session):
    # A pasted code carrying a stray space rode uncleaned into the Excel export and failed at
    # the ERP. The bulk path goes through the same _apply_correction, so it cannot differ.
    job, dump, document, cth = _setup(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value",
        "value": "  99999999  ", "previous": "10000001",
    })
    db_session.expire_all()
    assert dict(_values(db_session, job, cth))[1] == "99999999"


def test_an_unknown_field_is_a_404_not_a_silent_no_op(client, db_session):
    job, dump, document, cth = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": "no-such-field", "mode": "pick", "which": "other"})
    assert r.status_code == 404


# ---- a job with TWO invoices: a carry stays inside the invoice it was made on --------------

def _two_invoices(client, db_session):
    """Invoice 1 (set 1) and Invoice 2 (set 2), each with lines 1 and 2. Invoice 2's line 1 CTH
    happens to hold the same code as Invoice 1's line 1, so a carry that wrongly crossed over
    would have something to rewrite."""
    job, dump, document, cth = _setup(client, db_session, lines=2)
    for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all():
        fv.set_index = 1
    for n in (1, 2):
        for cf, extracted in (
            (dump, f"3000000{n}"),
            (document, f"4000000{n}"),
            (cth, "10000001" if n == 1 else f"3000000{n}"),
        ):
            db_session.add(JobFieldValue(
                tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cf.id,
                label_name=cf.label_name, set_index=2, row_index=n, extracted_value=extracted,
            ))
    db_session.commit()
    return job, dump, document, cth


def _by_set(db_session, job, cf):
    db_session.expire_all()
    out = {}
    for fv in db_session.query(JobFieldValue).filter(
            JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == cf.id).all():
        out[(fv.set_index or 1, fv.row_index)] = (
            fv.corrected_value if fv.corrected_value is not None else fv.extracted_value)
    return out


def test_a_pick_on_invoice_1_does_not_touch_invoice_2(client, db_session):
    job, dump, document, cth = _two_invoices(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other", "set_index": 1})
    assert r.status_code == 200, r.text
    assert r.json()["rows_written"] == 2
    assert _by_set(db_session, job, cth) == {
        (1, 1): "20000001", (1, 2): "20000002",           # Invoice 1 took the document side
        (2, 1): "10000001", (2, 2): "30000002",           # Invoice 2 exactly as it was
    }


def test_typing_a_cth_on_invoice_1_does_not_touch_invoice_2(client, db_session):
    """Invoice 2's line 1 holds the very code Invoice 1's line 1 held before the edit - the
    'was following along' rule alone would have rewritten it."""
    job, dump, document, cth = _two_invoices(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value", "value": "99999999",
        "previous": "10000001", "set_index": 1})
    got = _by_set(db_session, job, cth)
    assert got[(1, 1)] == "99999999"
    assert (got[(2, 1)], got[(2, 2)]) == ("10000001", "30000002")


def test_removing_a_cth_on_invoice_1_does_not_blank_invoice_2(client, db_session):
    job, dump, document, cth = _two_invoices(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": cth.id, "mode": "value", "value": "",
        "previous": "10000001", "set_index": 1})
    got = _by_set(db_session, job, cth)
    assert got[(1, 1)] == ""
    assert got[(2, 1)] == "10000001"


def test_a_pick_on_invoice_2_stays_on_invoice_2(client, db_session):
    job, dump, document, cth = _two_invoices(client, db_session)
    client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other", "set_index": 2})
    got = _by_set(db_session, job, cth)
    assert (got[(1, 1)], got[(1, 2)]) == ("10000001", "10000002")
    assert (got[(2, 1)], got[(2, 2)]) == ("40000001", "40000002")


def test_a_single_invoice_job_with_no_set_recorded_still_carries_to_every_line(client, db_session):
    """Lines of a one-invoice job carry no set_index at all; the screen sends set 1 for it.
    Blank set = 1, so every line is still reached."""
    job, dump, document, cth = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other", "set_index": 1})
    assert r.json()["rows_written"] == 3
    assert _values(db_session, job, cth) == [(1, "20000001"), (2, "20000002"), (3, "20000003")]

"""A rerun files a NEW entry in the ERP, so the job must take the new number.

Live, 2026-11-25. MAA/SI/03999/26-27 was re-run from the Entry Browser. The ERP filed it as a
brand new entry, MAA/SI/04008/26-27 - the old number is never reused - and the live browser
pane showed exactly that. Every other screen still said 03999: the jobs list, the job page,
the E-Docket's "JOB No.". So the operator who re-ran it went looking in SOFTLINK for a number
that was no longer this job's.

The promotion existed, but only for an untouched placeholder ("JOB-7F3A9C"). A job that had
already been submitted once had a real reference, so nothing moved it ever again.

What must NOT change: a reference a person typed themselves. The job's label is theirs. So the
rename is allowed only where the reference is one WE put there - a placeholder, or the number
the previous run read back off the ERP, which is why persist_run_outcome now remembers it
before the new run overwrites erp_captured.

The change is written to the job's history too, which is what puts it on the notification bell
(app/api/v1/notifications.py, kind "renumbered") - so the operator is told the number moved
rather than having to notice.
"""
from app.api.v1.jobs import RENUMBER_NOTE, _adopt_erp_job_no
from app.models.job import Job
from app.models.job_event import JobEvent
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _job(db_session, reference):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="completed")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _notes(db_session, job):
    return [
        e.note for e in
        db_session.query(JobEvent).filter(JobEvent.job_id == job.id).all()
        if (e.note or "").startswith(RENUMBER_NOTE)
    ]


def test_a_rerun_moves_the_reference_to_the_new_number(db_session):
    """THE production case: 03999 re-run, ERP filed 04008."""
    job = _job(db_session, "MAA/SI/03999/26-27")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/03999/26-27")
    db_session.commit()

    assert job.reference == "MAA/SI/04008/26-27"


def test_the_rerun_is_written_to_the_job_history(db_session):
    """Which is what the bell reads - and what the history popup shows a week later."""
    job = _job(db_session, "MAA/SI/03999/26-27")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/03999/26-27")
    db_session.commit()

    notes = _notes(db_session, job)
    assert len(notes) == 1, f"expected one renumber event, got {notes}"
    assert "MAA/SI/03999/26-27" in notes[0], "the operator needs the number they were given"
    assert "MAA/SI/04008/26-27" in notes[0], "and the one it is now"


def test_a_reference_a_person_typed_is_never_renamed(db_session):
    """The whole reason the previous number is passed in rather than guessed. An operator's
    own label for a job - a shipment number, a customer's booking ref - is theirs."""
    job = _job(db_session, "NOKIA SHIPMENT 42")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/03999/26-27")
    db_session.commit()

    assert job.reference == "NOKIA SHIPMENT 42"
    assert _notes(db_session, job) == [], "nothing changed, so nothing to announce"


def test_the_first_submission_still_promotes_the_placeholder(db_session):
    """Unchanged behaviour: a job auto-created from email carries JOB-XXXXXX until the ERP
    gives it a real one."""
    job = _job(db_session, "JOB-7F3A9C")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "")
    db_session.commit()

    assert job.reference == "MAA/SI/04008/26-27"


def test_promoting_a_placeholder_is_not_announced_as_a_change(db_session):
    """A placeholder was never a number anyone went looking for, and this happens on EVERY
    job's first submission - putting it on the bell would drown the renumbers that matter."""
    job = _job(db_session, "JOB-7F3A9C")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "")
    db_session.commit()

    assert _notes(db_session, job) == []


def test_the_same_number_coming_back_changes_nothing(db_session):
    """A rerun that somehow lands on the number the job already has is not a renumber, and
    must not add a history line saying it was."""
    job = _job(db_session, "MAA/SI/04008/26-27")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/04008/26-27")
    db_session.commit()

    assert job.reference == "MAA/SI/04008/26-27"
    assert _notes(db_session, job) == []


def test_a_run_with_no_job_number_leaves_the_reference_alone(db_session):
    """Belt and braces - persist_run_outcome already fails the run before it gets here, but
    an empty string must never become a job's name."""
    job = _job(db_session, "MAA/SI/03999/26-27")

    _adopt_erp_job_no(db_session, job, "", "MAA/SI/03999/26-27")
    db_session.commit()

    assert job.reference == "MAA/SI/03999/26-27"


def test_a_job_re_run_twice_records_both_moves(db_session):
    """Each rerun is its own event. The bell announces only the latest (see _renumbers), but
    the history keeps the whole chain - which is how anyone later works out that three ERP
    entries exist for this one job and two of them need deleting."""
    job = _job(db_session, "MAA/SI/03999/26-27")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04002/26-27", "MAA/SI/03999/26-27")
    db_session.commit()
    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/04002/26-27")
    db_session.commit()

    assert job.reference == "MAA/SI/04008/26-27"
    notes = _notes(db_session, job)
    assert len(notes) == 2, f"one per rerun - got {notes}"
    assert any("03999" in n and "04002" in n for n in notes)
    assert any("04002" in n and "04008" in n for n in notes)


def test_a_stale_previous_number_does_not_let_it_rename(db_session):
    """If the reference and the last captured number disagree, somebody changed the reference
    by hand after the last run. Their edit wins."""
    job = _job(db_session, "RENAMED BY HAND")

    _adopt_erp_job_no(db_session, job, "MAA/SI/04008/26-27", "MAA/SI/03999/26-27")
    db_session.commit()

    assert job.reference == "RENAMED BY HAND"

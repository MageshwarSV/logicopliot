"""A job still being filled from its email is not "Missing Documents".

The email puller has to commit the job before it can classify the attachments into slots -
it needs a real job id to hang them on, and OCR on a six-page pre-alert is not instant. For
those seconds the job is public, real, and has nothing in it yet.

"Missing Documents" then read that as paperwork that never arrived, so every freshly pulled
job appeared under Missing Documents and moved to New Jobs a moment later. To an operator
watching the list that looks like the system losing documents and finding them again.

The puller says what is happening through stage_override ("Document Classifying"), and an
explicit statement about where a job IS outranks a verdict derived from slots nobody has had
the chance to fill yet. The moment classification finishes the override is cleared and the
check applies normally again - including the case it was really written for, a document
removed from a job that had already been extracted.
"""
from app.api.v1.jobs import _is_missing_documents, _outer_status
from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _group_with_required_slot(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea import", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    doc = TemplateDocument(
        tenant_id=tenant.id, group_id=group.id, name="Invoice",
        doc_type="Invoice", is_required=True,
    )
    db_session.add(doc)
    db_session.commit()
    return group


def _job(db_session, tenant, group, status="draft", **kw):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-FILL", status=status, **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_job_with_no_documents_and_no_override_is_missing_documents(client, db_session):
    """The behaviour being protected: a job that really has nothing still says so."""
    tenant = make_tenant(db_session)
    group = _group_with_required_slot(db_session, tenant)
    job = _job(db_session, tenant, group)

    assert _is_missing_documents(db_session, job) is True
    assert _outer_status(db_session, job) == "Missing Documents"


def test_a_job_mid_classification_is_not_called_missing(client, db_session):
    """The fix. Same empty job, but the puller has said it is still working on it."""
    tenant = make_tenant(db_session)
    group = _group_with_required_slot(db_session, tenant)
    job = _job(db_session, tenant, group, stage_override="Document Classifying")

    assert _is_missing_documents(db_session, job) is False
    assert _outer_status(db_session, job) != "Missing Documents"


def test_it_becomes_missing_again_once_classification_finishes(client, db_session):
    """The override is not a permanent exemption. If the mail genuinely carried no invoice,
    the job must say so as soon as the puller stops working - otherwise this fix would hide
    real missing paperwork instead of a loading state."""
    tenant = make_tenant(db_session)
    group = _group_with_required_slot(db_session, tenant)
    job = _job(db_session, tenant, group, stage_override="Document Classifying")
    assert _is_missing_documents(db_session, job) is False

    job.stage_override = None  # what the puller does when it has finished
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert _is_missing_documents(db_session, job) is True
    assert _outer_status(db_session, job) == "Missing Documents"


def test_the_badge_and_the_list_bucket_always_agree(client, db_session):
    """_is_missing_documents exists to mirror _outer_status exactly. They are two separate
    pieces of code and drifted once already; if they disagree, a job shows one word on its
    badge and is counted under a different heading."""
    tenant = make_tenant(db_session)
    group = _group_with_required_slot(db_session, tenant)

    for override in (None, "Document Classifying", "ERP Submission"):
        job = Job(tenant_id=tenant.id, group_id=group.id,
                  reference=f"JOB-{override or 'NONE'}"[:40],
                  status="draft", stage_override=override)
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)

        by_helper = _is_missing_documents(db_session, job)
        by_badge = _outer_status(db_session, job) == "Missing Documents"
        assert by_helper == by_badge, (
            f"stage_override={override!r}: helper says {by_helper}, badge says {by_badge}"
        )


def test_a_finished_job_is_never_called_missing_whatever_its_slots_say(client, db_session):
    """Unchanged by this fix, and worth keeping honest: a completed job keeps its own word."""
    tenant = make_tenant(db_session)
    group = _group_with_required_slot(db_session, tenant)
    job = _job(db_session, tenant, group, status="completed")

    assert _is_missing_documents(db_session, job) is False

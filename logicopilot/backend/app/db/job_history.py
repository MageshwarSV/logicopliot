"""Record a job's status changes without asking anyone to remember to.

`job.status` is assigned in thirteen places across the jobs API, the email puller and the
background ERP runner. Writing a history row by hand at each of them would work until the
fourteenth was added, and the gap would show up as a job whose history quietly skips a step -
the sort of wrong that is only noticed when someone is trying to explain a delay to a customer.

So it is done once, at the session: before every flush, any Job whose `status` actually changed
gets a JobEvent. That covers paths written after this one, including any that do not know this
module exists.
"""
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from app.models.job import Job
from app.models.job_event import JobEvent

# What to call each status in the popup, so an operator is not reading the database's words.
# These are the ONLY words for a status: the API sends them with every event, because the
# screen used to keep a second copy and the two had drifted - one popup showed "Extracting" as
# a heading and "-> Extracting the documents" as the line under it, for the same event.
#
# `processing` deliberately does NOT say "Extracting the documents". One status covers two
# different jobs of work - pulling the documents apart, and driving the ERP entry - and the
# history cannot tell which from the status alone. It said "Extracting the documents" over
# every ERP entry ever run, so a completed entry read "Extracting the documents -> Completed".
# A word that is true of both is better than a precise word that is wrong half the time.
_WORDS = {
    "draft": "Created",
    "extracting": "AI - Processing",
    "processing": "Running",
    "extracted": "Documents extracted",
    "completed": "Completed",
    "failed": "Failed",
    "duplicate": "Already entered in the ERP",
}


def describe(status: str) -> str:
    return _WORDS.get(status, status or "")


def _changed_status(session: Session, obj) -> tuple[bool, str | None]:
    """Did this job's status really change, and what was it before?

    Two traps, both of which showed up as wrong history rather than as an error:

    `state.attrs.status.history` does not go to the database. After a commit the attribute is
    expired, so the previous value is simply absent and every line read "Extracting" instead of
    "Created -> Extracting". `get_history` with the default PASSIVE_OFF loads it.

    And an expired attribute assigned the value it already held still counts as dirty, because
    nothing compared them. A job saved twice on the same status wrote the same line twice, so
    the values are compared here rather than trusted.
    """
    hist = get_history(obj, "status")
    if not hist.has_changes():
        return False, None
    was = hist.deleted[0] if hist.deleted else None
    if was is None:
        # Assigning an EXPIRED attribute never loads what it replaced, so SQLAlchemy genuinely
        # does not know the old value and neither PASSIVE_OFF nor committed_state can produce
        # it. Read it from the row instead: a primary-key lookup on the connection the flush is
        # already using, which does not re-enter the flush.
        row = session.connection().execute(
            text("SELECT status FROM jobs WHERE id = :id"), {"id": obj.id}
        ).first()
        was = row[0] if row else None
    if was == obj.status:
        return False, was
    return True, was


@event.listens_for(Session, "before_flush")
def _write_job_history(session: Session, _flush_context, _instances) -> None:
    # New jobs first: a job's history has to start with its creation, or the popup opens on a
    # job that appears to have come from nowhere.
    for obj in list(session.new):
        if isinstance(obj, Job):
            # NOT session.add(JobEvent(job_id=obj.id, ...)): `id` is a Python-side default
            # applied at INSERT, so a job being created still has id None right here, and the
            # row failed on job_id NOT NULL. Going through the relationship lets SQLAlchemy
            # fill the key in once the job has one.
            obj.events.append(JobEvent(tenant_id=obj.tenant_id,
                                       status=obj.status or "draft", stage=None,
                                       note=describe(obj.status or "draft")))

    for obj in list(session.dirty):
        if not isinstance(obj, Job) or not session.is_modified(obj, include_collections=False):
            continue
        changed, was = _changed_status(session, obj)
        if not changed:
            continue
        note = describe(obj.status)
        if was and was != obj.status:
            note = f"{describe(was)} -> {describe(obj.status)}"
        session.add(JobEvent(tenant_id=obj.tenant_id, job_id=obj.id,
                             status=obj.status, stage=None, note=note))

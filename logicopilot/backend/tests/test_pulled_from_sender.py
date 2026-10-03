"""_pulled_from_sender() — the Jobs list's "Assigned To" column, for a job nobody has
touched yet. A job pulled from an email used to show a blank "Unassigned" with no way to tell
which of this tenant's own inboxes it actually came in on, until a real operator picked it
up. Showing the EXTERNAL SENDER instead (an earlier attempt) was the wrong thing to show -
the sender is a customer or a CHA with no account here at all, never something worth matching
against a user. This is the RECEIVING mailbox (meta["received_by"] - the shared inbox, e.g.
"cargora@4slogistics.com", or one operator's own connected mailbox), which is always one of
this tenant's own and answers what an operator actually wants to know at a glance: which of
our inboxes caught this paperwork.

If that mailbox belongs to a registered user (their login email, or a personal mailbox they
connected via User.mail_email), their own Full Name is shown instead of a raw fragment of the
address - a real account on file deserves its real name, not "cargora" guessed from
"cargora@4slogistics.com". Only a mailbox with no matching account falls back to that."""
import json

from app.api.v1.jobs import _pulled_from_sender
from app.core.job_email import job_email_dir
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import make_tenant, make_user


def _make_job(db_session, **kwargs):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SENDER", status="draft", **kwargs)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job, tenant


def _write_meta(job, received_by, sender="customer@example.com"):
    email_dir = job_email_dir(job.id)
    email_dir.mkdir(parents=True, exist_ok=True)
    (email_dir / "meta.json").write_text(
        json.dumps({"sender": sender, "received_by": received_by,
                   "subject": "FW: PRE ALERT", "attachments": []}),
        encoding="utf-8",
    )


def test_shows_the_receiving_mailbox_while_the_job_is_unassigned(db_session):
    job, _tenant = _make_job(db_session)
    _write_meta(job, "cargora@4slogistics.com")

    assert _pulled_from_sender(db_session, job) == "cargora"


def test_returns_none_once_a_real_operator_is_assigned(db_session):
    job, tenant = _make_job(db_session)
    _write_meta(job, "cargora@4slogistics.com")
    operator = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-pfs@example.com")
    job.assigned_operator_id = operator.id
    db_session.commit()

    assert _pulled_from_sender(db_session, job) is None


def test_returns_none_once_the_job_has_a_real_creator(db_session):
    job, tenant = _make_job(db_session)
    _write_meta(job, "cargora@4slogistics.com")
    creator = make_user(db_session, role=OPERATOR, tenant=tenant, email="creator-pfs@example.com")
    job.created_by_id = creator.id
    db_session.commit()

    assert _pulled_from_sender(db_session, job) is None


def test_a_job_not_pulled_from_any_email_returns_none(db_session):
    job, _tenant = _make_job(db_session)

    assert _pulled_from_sender(db_session, job) is None


def test_ignores_the_external_sender_entirely(db_session):
    """An email FROM an external customer/CHA address must never be matched against a user
    account - only the mailbox that RECEIVED it matters here."""
    job, tenant = _make_job(db_session)
    make_user(db_session, role=OPERATOR, tenant=tenant, email="ftwz2@4slogistics.com",
             full_name="Should Never Show")
    _write_meta(job, "cargora@4slogistics.com", sender="ftwz2@4slogistics.com")

    assert _pulled_from_sender(db_session, job) == "cargora"


def test_shows_the_users_full_name_when_the_receiving_mailbox_is_their_login_email(db_session):
    job, tenant = _make_job(db_session)
    make_user(db_session, role=OPERATOR, tenant=tenant, email="cargora@4slogistics.com",
             full_name="Cargora Shared Inbox")
    _write_meta(job, "cargora@4slogistics.com")

    assert _pulled_from_sender(db_session, job) == "Cargora Shared Inbox"


def test_matches_case_insensitively(db_session):
    job, tenant = _make_job(db_session)
    make_user(db_session, role=OPERATOR, tenant=tenant, email="cargora@4slogistics.com",
             full_name="Cargora Shared Inbox")
    _write_meta(job, "Cargora@4SLogistics.com")

    assert _pulled_from_sender(db_session, job) == "Cargora Shared Inbox"


def test_shows_the_users_full_name_when_the_receiving_mailbox_is_their_connected_mailbox(db_session):
    """A user's OWN mailbox (User.mail_email) is polled the same way the tenant's shared
    inbox is - see mail_email's own docstring - so it deserves the same name match as their
    login email, not just a raw fragment of the address."""
    job, tenant = _make_job(db_session)
    user = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-mailbox@example.com",
                     full_name="Arun Kumar")
    user.mail_email = "arun.personal@zoho.com"
    db_session.commit()
    _write_meta(job, "arun.personal@zoho.com")

    assert _pulled_from_sender(db_session, job) == "Arun Kumar"


def test_an_unregistered_mailbox_still_falls_back_to_the_raw_address(db_session):
    job, tenant = _make_job(db_session)
    make_user(db_session, role=OPERATOR, tenant=tenant, email="someone-else@example.com",
             full_name="Someone Else")
    _write_meta(job, "unregistered-inbox@4slogistics.com")

    assert _pulled_from_sender(db_session, job) == "unregistered-inbox"


def test_old_meta_with_no_received_by_falls_back_to_this_tenants_one_connected_mailbox(db_session):
    """A job pulled before this field existed has no recorded receiving mailbox at all - but
    if this TENANT has exactly one mailbox connected and active, every one of its older,
    unrecorded jobs unambiguously came in through it. Deliberately NOT the global GMAIL_USER
    default - that is a single platform-wide setting, wrong the moment a tenant actually polls
    through its own connected mailbox instead (the common case)."""
    job, tenant = _make_job(db_session)
    user = make_user(db_session, role=OPERATOR, tenant=tenant, email="cargora@4slogistics.com",
                     full_name="Cargora Shared Inbox")
    user.mail_email = "cargora@4slogistics.com"
    user.mail_paused = False
    db_session.commit()
    email_dir = job_email_dir(job.id)
    email_dir.mkdir(parents=True, exist_ok=True)
    (email_dir / "meta.json").write_text(
        json.dumps({"sender": "muthu@4slogistics.com", "subject": "FW: PRE ALERT", "attachments": []}),
        encoding="utf-8",
    )

    assert _pulled_from_sender(db_session, job) == "Cargora Shared Inbox"


def test_old_meta_with_no_received_by_and_multiple_tenant_mailboxes_returns_none(db_session):
    """More than one connected mailbox for this tenant means which one an older, unrecorded
    job actually came through is genuinely unknown - showing nothing is honest, picking one
    at random would not be."""
    job, tenant = _make_job(db_session)
    for i, addr in enumerate(("cargora@4slogistics.com", "ops@4slogistics.com")):
        u = make_user(db_session, role=OPERATOR, tenant=tenant, email=f"user{i}@example.com",
                      full_name=f"User {i}")
        u.mail_email = addr
        u.mail_paused = False
    db_session.commit()
    email_dir = job_email_dir(job.id)
    email_dir.mkdir(parents=True, exist_ok=True)
    (email_dir / "meta.json").write_text(
        json.dumps({"sender": "muthu@4slogistics.com", "subject": "FW: PRE ALERT", "attachments": []}),
        encoding="utf-8",
    )

    assert _pulled_from_sender(db_session, job) is None


def test_a_job_with_no_prealert_at_all_returns_none(db_session):
    """Not every job came from an email - a manual upload or an Excel entry has no prealert
    file, and this must not invent a mailbox for it from nothing."""
    job, _tenant = _make_job(db_session)

    assert _pulled_from_sender(db_session, job) is None

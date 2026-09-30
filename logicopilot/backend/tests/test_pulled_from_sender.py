"""_pulled_from_sender() — the Jobs list's "Assigned To" column, for a job nobody has
touched yet. A job pulled from a real person's email used to show a blank "Unassigned" with
no way to tell who it actually came from until a real operator picked it up. Showing that
sender under "Importer/Exporter" instead (an earlier attempt) was wrong for a different
reason: a person is not the consignee, and putting one there just answered a different
question under the wrong heading. This is the Assigned To column's own hint, and only
Assigned To's."""
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


def _write_sender(job, sender):
    email_dir = job_email_dir(job.id)
    email_dir.mkdir(parents=True, exist_ok=True)
    (email_dir / "meta.json").write_text(
        json.dumps({"sender": sender, "subject": "FW: PER ALERT", "attachments": []}),
        encoding="utf-8",
    )


def test_shows_the_sender_while_the_job_is_unassigned(db_session):
    job, _tenant = _make_job(db_session)
    _write_sender(job, "muthu@4slogistics.com")

    assert _pulled_from_sender(job) == "muthu"


def test_returns_none_once_a_real_operator_is_assigned(db_session):
    job, tenant = _make_job(db_session)
    _write_sender(job, "muthu@4slogistics.com")
    operator = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-pfs@example.com")
    job.assigned_operator_id = operator.id
    db_session.commit()

    assert _pulled_from_sender(job) is None


def test_returns_none_once_the_job_has_a_real_creator(db_session):
    job, tenant = _make_job(db_session)
    _write_sender(job, "muthu@4slogistics.com")
    creator = make_user(db_session, role=OPERATOR, tenant=tenant, email="creator-pfs@example.com")
    job.created_by_id = creator.id
    db_session.commit()

    assert _pulled_from_sender(job) is None


def test_a_job_not_pulled_from_any_email_returns_none(db_session):
    job, _tenant = _make_job(db_session)

    assert _pulled_from_sender(job) is None

"""A Super Admin's live pause switches for email pull and extraction - a database-backed
setting (not app.core.config.Settings, which is cached for the process lifetime) so a button
press takes effect on the very next call, no restart. Turning email pull's pause ON also
clears any message caught mid-read ("working" verdict): it was opened but nothing completed
for it, so it must be left retriable rather than silently stuck forever."""

from unittest.mock import MagicMock, patch

from app.core.email_puller import pull_inbox
from app.core.system_settings import get_system_settings, is_email_pull_paused, is_extraction_paused
from app.models.email_seen import EmailSeen
from tests.conftest import login, make_tenant, make_user


def test_system_settings_default_to_unpaused(db_session):
    row = get_system_settings(db_session)
    assert row.email_pull_paused is False
    assert row.extraction_paused is False


def test_getting_system_settings_twice_reuses_the_same_row(db_session):
    first = get_system_settings(db_session)
    first.email_pull_paused = True
    db_session.commit()
    second = get_system_settings(db_session)
    assert second.id == first.id
    assert second.email_pull_paused is True


def test_pull_inbox_does_nothing_at_all_when_email_pull_is_paused(db_session):
    row = get_system_settings(db_session)
    row.email_pull_paused = True
    db_session.commit()

    with patch("app.core.email_puller._connect") as mock_connect, \
         patch("app.core.email_puller._operator_mailboxes") as mock_mailboxes:
        result = pull_inbox(db_session)

    assert result["ok"] is True
    assert "paused" in result["note"].lower()
    # Not a single mailbox was even looked up, let alone connected to.
    mock_connect.assert_not_called()
    mock_mailboxes.assert_not_called()


def test_pull_inbox_runs_normally_when_not_paused(db_session):
    assert is_email_pull_paused(db_session) is False
    result = pull_inbox(db_session)
    assert result["ok"] is True
    assert "note" in result  # "no mailbox configured" - ran normally, just found nothing


def test_run_extraction_raises_when_extraction_is_paused(db_session):
    from app.api.v1.jobs import run_extraction
    from app.core.classifier import AIServiceUnavailable

    row = get_system_settings(db_session)
    row.extraction_paused = True
    db_session.commit()

    fake_job = MagicMock()
    fake_job.id = "job-1"
    try:
        run_extraction(db_session, fake_job)
        assert False, "expected AIServiceUnavailable"
    except AIServiceUnavailable as exc:
        assert "paused" in str(exc).lower()


def test_extraction_endpoint_refuses_cleanly_while_paused(client, db_session):
    from app.models.template_group import TemplateGroup
    from app.models.job import Job

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PAUSE-TEST", status="draft")
    db_session.add(job)
    db_session.commit()

    row = get_system_settings(db_session)
    row.extraction_paused = True
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="pause-op@example.com")
    login(client, op.email)
    resp = client.post(f"/api/v1/jobs/{job.id}/extract")
    assert resp.status_code == 503


def test_admin_toggle_endpoints_require_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-pause@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/system-settings")
    assert resp.status_code == 403


def test_toggling_email_pull_pause_on_via_api(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pause1@example.com")
    login(client, admin.email)

    resp = client.get("/api/v1/system-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_pull_paused"] is False
    assert body["extraction_paused"] is False
    assert body["openai_api_key_set"] is False
    assert body["email_poll_workers"] == 1
    assert body["max_email_poll_workers"] >= 1

    resp = client.post("/api/v1/system-settings/email-pull", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json()["email_pull_paused"] is True

    resp = client.get("/api/v1/system-settings")
    assert resp.json()["email_pull_paused"] is True


def test_toggling_extraction_pause_via_api(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-pause2@example.com")
    login(client, admin.email)

    resp = client.post("/api/v1/system-settings/extraction", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json()["extraction_paused"] is True

    resp = client.post("/api/v1/system-settings/extraction", json={"paused": False})
    assert resp.json()["extraction_paused"] is False


def test_pausing_email_pull_clears_stuck_working_claims_and_reports_the_count(client, db_session):
    db_session.add(EmailSeen(message_id="<stuck-1@example.com>", sender="a@x.com",
                             subject="stuck one", verdict="working"))
    db_session.add(EmailSeen(message_id="<stuck-2@example.com>", sender="b@x.com",
                             subject="stuck two", verdict="working"))
    db_session.add(EmailSeen(message_id="<done@example.com>", sender="c@x.com",
                             subject="already finished", verdict="matched"))
    db_session.commit()

    admin = make_user(db_session, role="super_admin", email="sa-pause3@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/email-pull", json={"paused": True})
    assert resp.status_code == 200
    assert resp.json()["cleared_in_progress"] == 2

    remaining = db_session.query(EmailSeen).all()
    assert {r.message_id for r in remaining} == {"<done@example.com>"}


def test_pausing_extraction_does_not_touch_email_seen_rows(client, db_session):
    db_session.add(EmailSeen(message_id="<untouched@example.com>", sender="a@x.com",
                             subject="working", verdict="working"))
    db_session.commit()

    admin = make_user(db_session, role="super_admin", email="sa-pause4@example.com")
    login(client, admin.email)
    client.post("/api/v1/system-settings/extraction", json={"paused": True})

    row = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<untouched@example.com>").one_or_none()
    assert row is not None

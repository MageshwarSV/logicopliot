"""How many mailboxes the email poller reads at once - a live, database-backed Super Admin
setting (Settings page), defaulting to 1, capped at this server's own CPU count so a number
the machine cannot sustain is refused outright rather than silently clamped."""

from unittest.mock import patch

import pytest

from app.core.system_settings import (
    InvalidWorkerCount,
    get_email_poll_workers,
    get_system_settings,
    max_email_poll_workers,
    set_email_poll_workers,
)
from tests.conftest import login, make_tenant, make_user


def test_worker_count_defaults_to_one(db_session):
    assert get_email_poll_workers(db_session) == 1


def test_max_workers_is_derived_from_cpu_count():
    with patch("os.cpu_count", return_value=4):
        assert max_email_poll_workers() == 4


def test_max_workers_never_goes_below_one_even_on_a_single_core_machine():
    with patch("os.cpu_count", return_value=None):
        assert max_email_poll_workers() == 1
    with patch("os.cpu_count", return_value=0):
        assert max_email_poll_workers() == 1


def test_setting_a_valid_worker_count_within_the_cap(db_session):
    with patch("app.core.system_settings.max_email_poll_workers", return_value=8):
        set_email_poll_workers(db_session, 5)
    assert get_email_poll_workers(db_session) == 5


def test_setting_a_worker_count_above_the_cap_is_refused(db_session):
    with patch("app.core.system_settings.max_email_poll_workers", return_value=4):
        with pytest.raises(InvalidWorkerCount):
            set_email_poll_workers(db_session, 5)
    # Refused means untouched - still at the default, not silently clamped to the cap.
    assert get_email_poll_workers(db_session) == 1


def test_setting_a_worker_count_of_zero_is_refused(db_session):
    with pytest.raises(InvalidWorkerCount):
        set_email_poll_workers(db_session, 0)


def test_setting_a_negative_worker_count_is_refused(db_session):
    with pytest.raises(InvalidWorkerCount):
        set_email_poll_workers(db_session, -1)


def test_setting_exactly_the_cap_is_allowed(db_session):
    with patch("app.core.system_settings.max_email_poll_workers", return_value=4):
        set_email_poll_workers(db_session, 4)
    assert get_email_poll_workers(db_session) == 4


def test_workers_endpoint_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-workers@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/workers", json={"count": 2})
    assert resp.status_code == 403


def test_workers_endpoint_sets_a_valid_count(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-workers1@example.com")
    login(client, admin.email)
    with patch("app.core.system_settings.max_email_poll_workers", return_value=8):
        resp = client.post("/api/v1/system-settings/workers", json={"count": 3})
    assert resp.status_code == 200
    assert resp.json()["email_poll_workers"] == 3

    resp = client.get("/api/v1/system-settings")
    assert resp.json()["email_poll_workers"] == 3


def test_workers_endpoint_returns_400_for_a_count_above_the_cap(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-workers2@example.com")
    login(client, admin.email)
    with patch("app.core.system_settings.max_email_poll_workers", return_value=2):
        resp = client.post("/api/v1/system-settings/workers", json={"count": 99})
    assert resp.status_code == 400
    assert "2" in resp.json()["detail"]


def test_system_settings_get_reports_the_real_server_max(client, db_session):
    # read_system_settings imports max_email_poll_workers directly into its own module
    # namespace, so the patch target for THIS call is the router module, not the source -
    # unlike set_email_poll_workers's own internal call, which resolves within
    # app.core.system_settings itself and is covered by the other tests in this file.
    admin = make_user(db_session, role="super_admin", email="sa-workers3@example.com")
    login(client, admin.email)
    with patch("app.api.v1.system_settings.max_email_poll_workers", return_value=6):
        resp = client.get("/api/v1/system-settings")
    assert resp.json()["max_email_poll_workers"] == 6

"""How many real ERP submissions (browser automation) run at once - a live, database-backed
Super Admin setting (Settings page), defaulting to 1, capped at this server's own CPU count.
See app/core/erp_queue.py for the shared pool this setting actually governs."""
import threading
import time
from unittest.mock import patch

import pytest

from app.core.erp_queue import submit_erp_run
from app.core.system_settings import (
    InvalidWorkerCount,
    get_erp_script_workers,
    get_system_settings,
    max_erp_script_workers,
    set_erp_script_workers,
)
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def test_worker_count_defaults_to_one(db_session):
    assert get_erp_script_workers(db_session) == 1


def test_max_workers_is_derived_from_cpu_count():
    with patch("os.cpu_count", return_value=4):
        assert max_erp_script_workers() == 4


def test_max_workers_never_goes_below_one_even_on_a_single_core_machine():
    with patch("os.cpu_count", return_value=None):
        assert max_erp_script_workers() == 1
    with patch("os.cpu_count", return_value=0):
        assert max_erp_script_workers() == 1


def test_setting_a_valid_worker_count_within_the_cap(db_session):
    with patch("app.core.system_settings.max_erp_script_workers", return_value=8):
        set_erp_script_workers(db_session, 5)
    assert get_erp_script_workers(db_session) == 5


def test_setting_a_worker_count_above_the_cap_is_refused(db_session):
    with patch("app.core.system_settings.max_erp_script_workers", return_value=4):
        with pytest.raises(InvalidWorkerCount):
            set_erp_script_workers(db_session, 5)
    assert get_erp_script_workers(db_session) == 1


def test_setting_a_worker_count_of_zero_is_refused(db_session):
    with pytest.raises(InvalidWorkerCount):
        set_erp_script_workers(db_session, 0)


def test_setting_a_negative_worker_count_is_refused(db_session):
    with pytest.raises(InvalidWorkerCount):
        set_erp_script_workers(db_session, -1)


def test_erp_workers_endpoint_requires_super_admin(client, db_session):
    tenant = make_tenant(db_session)
    admin = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-erpworkers@example.com")
    login(client, admin.email)
    resp = client.post("/api/v1/system-settings/erp-workers", json={"count": 2})
    assert resp.status_code == 403


def test_erp_workers_endpoint_sets_a_valid_count(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-erpworkers1@example.com")
    login(client, admin.email)
    with patch("app.core.system_settings.max_erp_script_workers", return_value=8):
        resp = client.post("/api/v1/system-settings/erp-workers", json={"count": 3})
    assert resp.status_code == 200
    assert resp.json()["erp_script_workers"] == 3

    resp = client.get("/api/v1/system-settings")
    assert resp.json()["erp_script_workers"] == 3


def test_erp_workers_endpoint_returns_400_for_a_count_above_the_cap(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-erpworkers2@example.com")
    login(client, admin.email)
    with patch("app.core.system_settings.max_erp_script_workers", return_value=2):
        resp = client.post("/api/v1/system-settings/erp-workers", json={"count": 99})
    assert resp.status_code == 400
    assert "2" in resp.json()["detail"]


def test_system_settings_get_reports_the_real_erp_worker_max(client, db_session):
    admin = make_user(db_session, role="super_admin", email="sa-erpworkers3@example.com")
    login(client, admin.email)
    with patch("app.api.v1.system_settings.max_erp_script_workers", return_value=6):
        resp = client.get("/api/v1/system-settings")
    assert resp.json()["max_erp_script_workers"] == 6


# --------------------------------------------------------------- the shared pool itself


def test_pool_with_one_worker_runs_jobs_one_at_a_time_not_concurrently():
    """The actual safety property this whole feature exists for: with exactly one worker,
    a second submission must not start until the first one has finished."""
    concurrent_count = 0
    max_concurrent = 0
    lock = threading.Lock()
    done = threading.Event()

    def make_task(is_last: bool):
        def task():
            nonlocal concurrent_count, max_concurrent
            with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
            time.sleep(0.2)
            with lock:
                concurrent_count -= 1
            if is_last:
                done.set()
        return task

    submit_erp_run(1, make_task(False))
    submit_erp_run(1, make_task(True))
    assert done.wait(timeout=5), "second task never ran"
    assert max_concurrent == 1


def test_two_real_submissions_serialize_through_the_actual_endpoint(client, db_session, session_factory):
    """End-to-end: two DIFFERENT jobs approved moments apart, with the worker count set to 1,
    must not run their ERP submissions concurrently - this is the actual safety property the
    whole feature exists for, exercised through the real gk2/approve endpoint rather than
    calling the pool directly."""
    set_erp_script_workers(db_session, 1)

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    def make_job():
        job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-POOL", status="extracted",
                  validation_approved=True, gk2_status="pending", gk2_validation_approved=True)
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return job

    job1 = make_job()
    job2 = make_job()
    gk2 = make_user(db_session, role="gk2", tenant=tenant, email="gk2-pool@example.com")
    gk2.assigned_modes = ["Sea Import"]
    db_session.add(gk2)
    db_session.commit()
    login(client, gk2.email)

    concurrent_count = 0
    max_concurrent = 0
    lock = threading.Lock()
    finished = threading.Event()
    finished_count = 0

    def fake_run_gk2_submission(db2, job_id, is_excel_entry):
        nonlocal concurrent_count, max_concurrent, finished_count
        with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        # NOT time.sleep - this test patches time.sleep globally (same as every other test in
        # this file that uses the gk2_approve + SessionLocal patch pattern), which would make
        # this delay a no-op too and defeat the whole point of creating an observable overlap
        # window. threading.Event.wait() is a real, unpatched block.
        threading.Event().wait(0.3)
        with lock:
            concurrent_count -= 1
            finished_count += 1
            if finished_count == 2:
                finished.set()

    with patch("time.sleep", return_value=None), \
         patch("app.db.session.SessionLocal", session_factory), \
         patch("app.api.v1.jobs._run_gk2_submission", side_effect=fake_run_gk2_submission):
        resp1 = client.post(f"/api/v1/jobs/{job1.id}/gk2/approve")
        assert resp1.status_code == 200, resp1.text
        resp2 = client.post(f"/api/v1/jobs/{job2.id}/gk2/approve")
        assert resp2.status_code == 200, resp2.text
        assert finished.wait(timeout=5), "both submissions never finished"

    assert max_concurrent == 1, "two ERP submissions ran concurrently despite erp_script_workers=1"


def test_pool_resizes_when_worker_count_changes():
    """Changing the setting takes effect on the next submission without needing a restart -
    the pool is rebuilt in place rather than fixed at process start."""
    seen_workers = []
    done = threading.Event()

    def task():
        seen_workers.append(1)
        if len(seen_workers) == 2:
            done.set()

    submit_erp_run(1, task)
    submit_erp_run(3, task)  # worker count "changed" - should still run, just on a new pool
    assert done.wait(timeout=5)
    assert len(seen_workers) == 2

"""PROCESS_ROLE: which background work a process starts when the app runs split over several
servers. "all" (default) must behave exactly as before; each part has exactly one owner; a typo
starts nothing."""
from types import SimpleNamespace

import pytest

from app.core import process_role, restart_resume


def _set(monkeypatch, value, redis_url=""):
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: SimpleNamespace(process_role=value, redis_url=redis_url))


@pytest.mark.parametrize("value,expected", [
    ("all", {"mail", "pipeline", "extraction_resume", "erp"}),
    ("web", set()),
    ("worker", {"mail", "pipeline", "extraction_resume"}),
    ("erp", {"erp"}),
    (" ERP ", {"erp"}),          # spaces / case from a hand-edited .env
    ("", {"mail", "pipeline", "extraction_resume", "erp"}),   # unset = all
    ("wroker", set()),           # typo: nothing, never a second mail reader
])
def test_parts_per_role(monkeypatch, value, expected):
    _set(monkeypatch, value)
    assert {p for p in process_role.PART_OWNER if process_role.runs(p)} == expected


def test_every_part_has_exactly_one_split_owner(monkeypatch):
    for part in process_role.PART_OWNER:
        owners = []
        for r in ("web", "worker", "erp"):
            _set(monkeypatch, r)
            if process_role.runs(part):
                owners.append(r)
        assert len(owners) == 1, (part, owners)


@pytest.mark.parametrize("value,name", [
    ("all", "singleton_worker.lock"),       # unchanged file for today's servers
    ("web", "singleton_worker-web.lock"),
    ("worker", "singleton_worker-worker.lock"),
    ("erp", "singleton_worker-erp.lock"),
])
def test_lock_file_per_role(monkeypatch, value, name):
    _set(monkeypatch, value)
    assert process_role.lock_name() == name


def test_worker_and_erp_on_one_machine_both_get_their_lock(monkeypatch, tmp_path):
    """The 1-machine SIT test runs three processes from one checkout; each role must win its
    own lock instead of the first process taking the only one."""
    import fcntl  # noqa: F401 - POSIX only, same as the servers

    import app.core.worker_lock as wl

    monkeypatch.setattr(wl, "_LOCK_PATH", tmp_path / "singleton_worker.lock")
    held = []
    for r in ("worker", "erp"):
        monkeypatch.setattr(wl, "_held_handle", None)
        _set(monkeypatch, r)
        assert wl.am_i_the_singleton_worker() is True
        held.append(wl._held_handle)
    # a second process of the SAME role is refused
    monkeypatch.setattr(wl, "_held_handle", None)
    _set(monkeypatch, "erp")
    assert wl.am_i_the_singleton_worker() is False
    for h in held:
        h.close()


def test_resume_only_the_requested_kinds(monkeypatch):
    calls = []
    monkeypatch.setattr(restart_resume, "_resume_extractions", lambda db: calls.append("x") or [])
    monkeypatch.setattr(restart_resume, "_resume_erp_runs", lambda db: calls.append("e") or [])
    monkeypatch.setattr("app.db.session.SessionLocal",
                        lambda: SimpleNamespace(close=lambda: None, rollback=lambda: None))
    restart_resume.resume_interrupted_work(("extraction",))
    assert calls == ["x"]
    calls.clear()
    restart_resume.resume_interrupted_work(("ERP",))
    assert calls == ["e"]
    calls.clear()
    restart_resume.resume_interrupted_work()      # default: both, as before
    assert calls == ["x", "e"]


def test_split_without_redis_is_reported(monkeypatch, caplog):
    _set(monkeypatch, "worker", redis_url="")
    with caplog.at_level("WARNING"):
        process_role.startup_report()
    assert "without REDIS_URL" in caplog.text
    caplog.clear()
    _set(monkeypatch, "all")
    process_role.startup_report()
    assert caplog.text == ""                      # today's servers log nothing new

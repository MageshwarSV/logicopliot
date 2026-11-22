"""ERP_SESSIONS_DIR: ERP session files + lock files default to uploads/erp_sessions (as before);
a split ERP server points them at its own local disk instead of the shared NFS uploads."""
from types import SimpleNamespace

from app.core import config


def _set(monkeypatch, uploads, sessions=""):
    monkeypatch.setattr(config, "get_settings",
                        lambda: SimpleNamespace(uploads_dir=str(uploads), erp_sessions_dir=sessions))


def test_default_is_under_uploads(monkeypatch, tmp_path):
    _set(monkeypatch, tmp_path / "uploads")
    d = config.erp_sessions_dir()
    assert d == tmp_path / "uploads" / "erp_sessions" and d.is_dir()


def test_local_dir_when_set(monkeypatch, tmp_path):
    _set(monkeypatch, tmp_path / "nfs", sessions=str(tmp_path / "local" / "erp_sessions"))
    d = config.erp_sessions_dir()
    assert d == tmp_path / "local" / "erp_sessions" and d.is_dir()
    assert not (tmp_path / "nfs" / "erp_sessions").exists()


def test_lock_files_follow_the_setting(monkeypatch, tmp_path):
    from app.core import branch_sessions, erp_session

    _set(monkeypatch, tmp_path / "nfs", sessions=str(tmp_path / "local"))
    assert erp_session._run_lock_path("s1").startswith(str(tmp_path / "local"))
    assert branch_sessions._dir() == tmp_path / "local"

"""Large-job lane (step 1): which mail goes to the lane, no duplicates, classification on the
lane does not take a shared worker, Run extraction on a lane job only leaves a marker."""
import threading
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from types import SimpleNamespace

import pytest

from app.core import email_puller, large_job_lane
from app.core.extraction_queue import run_queued


def _mail(zip_bytes: int, mid: str = "<m1@test>") -> bytes:
    msg = MIMEMultipart()
    msg["Subject"] = "Flex DTA 312-93004472"
    msg["Message-ID"] = mid
    msg.attach(MIMEText("see attached"))
    if zip_bytes:
        part = MIMEApplication(b"z" * zip_bytes, Name="bundle.zip")
        part["Content-Disposition"] = 'attachment; filename="bundle.zip"'
        msg.attach(part)
    return msg.as_bytes()


class _Q:
    def __init__(self, row):
        self.row = row

    def filter(self, *_a):
        return self

    def first(self):
        return self.row


class _DB:
    """get() for the template, query().filter().first() for the EmailSeen verdict."""
    def __init__(self, groups, verdict=None):
        self.groups = groups
        self.verdict = verdict

    def get(self, _cls, gid):
        return self.groups.get(gid)

    def query(self, *_a):
        return _Q((self.verdict,) if self.verdict else None)


@pytest.fixture
def lane(monkeypatch):
    """A clean lane that records submissions instead of running them."""
    monkeypatch.setattr(large_job_lane, "_emails", large_job_lane.deque())
    monkeypatch.setattr(large_job_lane, "_queued_keys", set())
    monkeypatch.setattr(large_job_lane, "start", lambda: None)
    monkeypatch.setattr(large_job_lane, "_write_status", lambda: None)
    import app.core.worker_lock as wl

    monkeypatch.setattr(wl, "am_i_the_singleton_worker", lambda: True)
    return large_job_lane


MB = 1024 * 1024
ON = SimpleNamespace(large_job_lane=True, large_zip_mb=5)
OFF = SimpleNamespace(large_job_lane=False, large_zip_mb=5)


def _task(gid, size, mid="<m1@test>", **extra):
    return {"src": {"group_id": gid}, "raw": _mail(size, mid), "uid": b"1", **extra}


def test_big_zip_on_lane_template_goes_to_lane(lane):
    db = _DB({"g": ON})
    keep = email_puller._hand_large_mail_to_lane(db, [_task("g", 6 * MB)], False, lambda t: None)
    assert keep == []
    assert len(lane._emails) == 1 and "6.0 MB" in lane._emails[0]["label"]


@pytest.mark.parametrize("gid,size,extra,reexamine", [
    ("g", 4 * MB, {}, False),                 # small zip
    ("g", 0, {}, False),                      # no zip at all
    ("off", 9 * MB, {}, False),               # template without the lane
    (None, 9 * MB, {}, False),                # shared inbox - template not known yet
    ("g", 9 * MB, {"parent_mid": "p"}, False),  # a pre-alert split part
    ("g", 9 * MB, {}, True),                  # a manual re-examine
])
def test_everything_else_stays_in_the_cycle(lane, gid, size, extra, reexamine):
    db = _DB({"g": ON, "off": OFF})
    task = _task(gid, size, **extra)
    keep = email_puller._hand_large_mail_to_lane(db, [task], reexamine, lambda t: None)
    assert keep == [task]
    assert len(lane._emails) == 0


def test_not_the_singleton_process_keeps_everything(lane, monkeypatch):
    import app.core.worker_lock as wl

    monkeypatch.setattr(wl, "am_i_the_singleton_worker", lambda: False)
    task = _task("g", 9 * MB)
    assert email_puller._hand_large_mail_to_lane(_DB({"g": ON}), [task], False, lambda t: None) == [task]


def test_same_mail_is_not_queued_twice(lane):
    db = _DB({"g": ON})
    email_puller._hand_large_mail_to_lane(db, [_task("g", 9 * MB)], False, lambda t: None)
    # next cycle finds the same unclaimed mail again
    keep = email_puller._hand_large_mail_to_lane(db, [_task("g", 9 * MB)], False, lambda t: None)
    assert keep == [] and len(lane._emails) == 1


def test_mixed_cycle_only_the_big_one_leaves(lane):
    db = _DB({"g": ON})
    small = _task("g", 1 * MB, mid="<small@test>")
    big = _task("g", 9 * MB, mid="<big@test>")
    keep = email_puller._hand_large_mail_to_lane(db, [small, big], False, lambda t: None)
    assert keep == [small] and len(lane._emails) == 1


def test_classification_on_the_lane_runs_on_the_lane_thread():
    here = threading.current_thread().name
    large_job_lane._tl.active = True
    try:
        assert run_queued(lambda: threading.current_thread().name) == here
    finally:
        large_job_lane._tl.active = False
    # off the lane it still goes to the shared pool
    assert run_queued(lambda: threading.current_thread().name) != here


def test_is_lane_job():
    job = SimpleNamespace(attachment_notes={"large_zip": {"size_mb": 9.6}, "large_job": True})
    assert large_job_lane.is_lane_job(job, ON)
    assert not large_job_lane.is_lane_job(job, OFF)
    assert not large_job_lane.is_lane_job(job, None)
    assert not large_job_lane.is_lane_job(SimpleNamespace(attachment_notes={}), ON)
    # a big Smart Upload: large zip noted, but never read on the lane
    upload = SimpleNamespace(attachment_notes={"large_zip": {"size_mb": 12.0}})
    assert not large_job_lane.is_lane_job(upload, ON)


def test_request_extraction_only_leaves_a_marker(monkeypatch):
    monkeypatch.setattr(large_job_lane, "_wake", threading.Event())
    commits = []
    db = SimpleNamespace(commit=lambda: commits.append(1))
    job = SimpleNamespace(reference="JOB-1", attachment_notes={"large_zip": {"size_mb": 9.6}})
    large_job_lane.request_extraction(db, job, is_rerun=True)
    assert job.attachment_notes["lane_extract"]["is_rerun"] is True
    assert job.attachment_notes["large_zip"] == {"size_mb": 9.6}
    assert commits == [1]


def test_already_processed_mail_stays_in_the_cycle(lane):
    """Processed mail stays unread in the inbox, so every poll finds it again - the cycle's
    _claim skips it; it must not be handed to the lane each minute."""
    task = _task("g", 9 * MB)
    keep = email_puller._hand_large_mail_to_lane(_DB({"g": ON}, verdict="matched"), [task], False, lambda t: None)
    assert keep == [task] and len(lane._emails) == 0
    # being read right now by the lane: also left to the cycle (its _claim says "reading it now")
    keep = email_puller._hand_large_mail_to_lane(_DB({"g": ON}, verdict="working"), [task], False, lambda t: None)
    assert keep == [task] and len(lane._emails) == 0


def test_mail_held_failed_goes_back_to_the_lane(lane):
    keep = email_puller._hand_large_mail_to_lane(_DB({"g": ON}, verdict="failed"), [_task("g", 9 * MB)], False, lambda t: None)
    assert keep == [] and len(lane._emails) == 1


def test_status_writes_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(large_job_lane, "_status_path", lambda: tmp_path / "status.json")
    errors = []
    monkeypatch.setattr(large_job_lane.logger, "exception", lambda *a, **k: errors.append(a))
    threads = [threading.Thread(target=large_job_lane._write_status) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and (tmp_path / "status.json").exists()

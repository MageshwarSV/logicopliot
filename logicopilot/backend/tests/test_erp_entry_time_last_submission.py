"""ERP Entry Time on the Job Processing Time / Workforce dashboards is measured from the LAST
ERP submission to completion, never from the first.

Found on production: MAA/SI/03737/26-27 showed 502h (first submitted 22 Oct, failed and
retried, finally completed 12 Nov) and MAA/SI/03370/26-27 showed 624h, because the old formula
used ai_submitted_at, which only ever keeps the first submission.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.dashboard_metrics import _erp_entry_minutes

T0 = datetime(2026, 10, 22, 16, 47, tzinfo=timezone.utc)


def _job(*, completed, erp_submitted=None, ai_submitted=None):
    return SimpleNamespace(completed_at=completed, erp_submitted_at=erp_submitted, ai_submitted_at=ai_submitted)


def _ev(at, status):
    return SimpleNamespace(created_at=at, status=status)


def test_uses_last_submission_not_first():
    # First submitted T0, resubmitted 20 days later, entry took 7 minutes.
    last = T0 + timedelta(days=20)
    job = _job(ai_submitted=T0, erp_submitted=last, completed=last + timedelta(minutes=7))
    events = [_ev(T0 + timedelta(days=4), "failed"), _ev(last + timedelta(minutes=7), "completed")]
    assert _erp_entry_minutes(job, events) == 7


def test_single_submission_same_instant_is_zero():
    job = _job(erp_submitted=T0, completed=T0)
    assert _erp_entry_minutes(job, []) == 0


def test_not_completed_is_none():
    assert _erp_entry_minutes(_job(erp_submitted=T0, completed=None), []) is None


def test_completed_before_last_submission_is_none():
    # Resubmitted, not finished yet: completed_at is still the earlier completion.
    job = _job(erp_submitted=T0 + timedelta(days=2), completed=T0)
    assert _erp_entry_minutes(job, []) is None


def test_legacy_job_submitted_once_keeps_first_submission():
    # Completed before erp_submitted_at existed, submitted exactly once: ai_submitted_at IS
    # the last submission.
    job = _job(ai_submitted=T0, completed=T0 + timedelta(minutes=3))
    events = [_ev(T0 + timedelta(minutes=3), "completed")]
    assert _erp_entry_minutes(job, events) == 3


def test_legacy_job_resubmitted_is_none_not_the_whole_gap():
    # The MAA/SI/03737 shape: first submission, then failed/duplicate, completed weeks later.
    # Its last submission time was never recorded, so no number rather than 502h.
    end = T0 + timedelta(days=20, hours=22)
    job = _job(ai_submitted=T0, completed=end)
    events = [
        _ev(T0, "duplicate"),
        _ev(T0 + timedelta(days=4), "failed"),
        _ev(T0 + timedelta(days=4), "extracted"),
        _ev(end, "completed"),
    ]
    assert _erp_entry_minutes(job, events) is None


def test_legacy_job_completed_then_reopened_is_none():
    # The MAA/SI/03370 shape: completed at the first submission, reopened (Completed -> Failed)
    # and completed again much later.
    end = T0 + timedelta(days=26)
    job = _job(ai_submitted=T0, completed=end)
    events = [_ev(T0, "completed"), _ev(T0 + timedelta(days=18), "failed"), _ev(end, "completed")]
    assert _erp_entry_minutes(job, events) is None


def test_no_submission_recorded_is_none():
    assert _erp_entry_minutes(_job(completed=T0), []) is None
